"""
Covered-Call IV Realism Study — R&D #26 follow-up, Item 2 (flat-IV booked-P&L bias).

The covered-call engine prices every weekly premium with a FLAT sigma = 0.30
(aether/options.py FLAT_SIGMA) and books the premium as REAL cash at write
(state["balance"] += premium_usd). A single flat vol systematically over-credits
calm blue chips and under-credits volatile names, feeding the ledger (and any future
optimizer) a bias. This is a PRICING-REALISM correction, not an alpha factor, so it
has no forward-return spread to pass/fail — this study CALIBRATES the fix rather than
gating it (per the split-gating decision).

It answers, from history, with pure price data and no look-ahead:
  1. How far is each symbol's realized volatility from the flat 0.30? (the over-credit
     ratio flat/realized, bucketed by vol tier — tests the "~2x on calm names" claim.)
  2. What calibration constant K maps the ATR proxy (ATR/price)*sqrt(252) onto realized
     close-to-close vol, so the engine can use sigma = clamp((ATR/price)*sqrt(252)*K)?
  3. What are sane floor/ceiling clamps for the per-symbol sigma?

Realized vol = annualized sample std of daily log returns (trailing 20d and 60d).
ATR proxy    = (ATR14 / close) * sqrt(252), computed at the same bar.
Series are split-adjusted (aether.risk_utils._split_adjust_ohlcv) and synthetic
placeholder bars (volume==0 / open==high==low==close) are excluded from every window.

Usage:
    python scripts/backtesting/covered_call_iv_study.py

Writes Data/covered_call_iv_study.json (meta with recommended IV_ATR_K / vol_floor /
vol_ceiling, per-vol-tier tables, and per-symbol summaries). Re-run MONTHLY after the
OHLCV top-up (the calibration is drawn from 25+yr x 500+ symbols and does not move
week-to-week), then update IV_ATR_K / IV_FLOOR / IV_CEILING in aether/options.py.
"""

import json
import math
import os
import sys
from pathlib import Path


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import instruments
from aether.options import calculate_black_scholes_call
from aether.risk_utils import _split_adjust_ohlcv
from aether_logger import get_logger as _get_logger


_log = _get_logger("cc_iv_study")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
OHLCV    = Path(os.environ.get("AETHER_OHLCV_DIR", str(BASE_DIR / "Data" / "Symbol_full")))
OUT_FILE = BASE_DIR / "Data" / "covered_call_iv_study.json"

FLAT_SIGMA   = 0.30    # the current engine placeholder we are testing
TRADING_DAYS = 252
RV_WIN       = 20      # trailing window for realized vol (primary)
RV_WIN_LONG  = 60      # secondary realized-vol window
ATR_PERIOD   = 14
STRIDE       = 21      # sample ~monthly to avoid O(n) overcount of autocorrelated bars
WARMUP       = RV_WIN_LONG + 2
MIN_SAMPLES  = 6       # a symbol needs at least this many clean samples to count

# Representative option for the dollar-bias illustration: 5% OTM weekly (matches the
# engine's 1.05x floor and T = 7/365 in select_covered_call).
BIAS_PRICE   = 100.0
BIAS_STRIKE  = 105.0
BIAS_T       = 7.0 / 365.0
BIAS_RATE    = 0.04


def _bars(path):
    """Return split-adjusted parallel arrays (o, h, l, c, v) or None."""
    try:
        with open(path) as f:
            ts = json.load(f).get("Time Series (Daily)", {})
    except Exception:
        return None
    dates = sorted(ts.keys())
    if len(dates) < WARMUP + STRIDE:
        return None
    o, h, l, c, v = [], [], [], [], []
    for d in dates:
        b = ts[d]
        try:
            o.append(float(b["1. open"])); h.append(float(b["2. high"]))
            l.append(float(b["3. low"]));  c.append(float(b["4. close"]))
            v.append(float(b.get("5. volume", 0) or 0))
        except (KeyError, ValueError):
            return None
    h, l, c = _split_adjust_ohlcv(o, h, l, c)
    return o, h, l, c, v


def _rolling_atr(h, l, c):
    """atr[i] = simple mean of True Range over the trailing ATR_PERIOD bars (None until warm)."""
    n = len(c)
    tr = [0.0] * n
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    atr = [None] * n
    run = 0.0
    for i in range(1, n):
        run += tr[i]
        if i > ATR_PERIOD:
            run -= tr[i - ATR_PERIOD]
        if i >= ATR_PERIOD:
            atr[i] = run / ATR_PERIOD
    return atr


def _clean_window(o, h, l, c, v, a, b):
    """True if bars [a, b] carry no synthetic placeholder (vol==0 or o==h==l==c)."""
    for i in range(a, b + 1):
        if v[i] <= 0 or (o[i] == h[i] == l[i] == c[i]):
            return False
    return True


def _realized_vol(c, i, win):
    """Annualized sample std of daily log returns over the `win` returns ending at bar i."""
    rets = []
    for j in range(i - win + 1, i + 1):
        if c[j] > 0 and c[j - 1] > 0:
            rets.append(math.log(c[j] / c[j - 1]))
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(TRADING_DAYS)


def _median(xs):
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return None
    m = n // 2
    return s[m] if n % 2 else (s[m - 1] + s[m]) / 2.0


def _pct(xs, p):
    s = sorted(xs)
    if not s:
        return None
    k = (len(s) - 1) * p
    lo = int(math.floor(k)); hi = int(math.ceil(k))
    if lo == hi:
        return s[lo]
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def _samples_for_symbol(arrays):
    """Yield (rv20, rv60, atr_proxy) at a monthly stride, skipping synthetic windows."""
    o, h, l, c, v = arrays
    atr = _rolling_atr(h, l, c)
    n = len(c)
    out = []
    for i in range(WARMUP, n, STRIDE):
        if atr[i] is None or c[i] <= 0:
            continue
        lo = i - max(RV_WIN_LONG, ATR_PERIOD)
        if lo < 1 or not _clean_window(o, h, l, c, v, lo, i):
            continue
        rv20 = _realized_vol(c, i, RV_WIN)
        rv60 = _realized_vol(c, i, RV_WIN_LONG)
        if rv20 is None or rv60 is None:
            continue
        atr_proxy = (atr[i] / c[i]) * math.sqrt(TRADING_DAYS)
        if atr_proxy <= 0:
            continue
        out.append((rv20, rv60, atr_proxy))
    return out


def run():
    files = sorted(OHLCV.glob("*_daily.json"))
    _log.console(f"Scanning {len(files)} symbols for realized-vol vs flat-{FLAT_SIGMA} IV...\n")

    per_symbol = []
    all_rv, all_ap = [], []   # pooled samples for the K regression
    n_used = 0
    for path in files:
        sym = path.stem.replace("_daily", "")
        try:
            if instruments.is_excluded(sym):
                continue
        except Exception:
            pass
        arrays = _bars(path)
        if not arrays:
            continue
        samples = _samples_for_symbol(arrays)
        if len(samples) < MIN_SAMPLES:
            continue
        n_used += 1
        rv20s = [s[0] for s in samples]
        rv60s = [s[1] for s in samples]
        aps   = [s[2] for s in samples]
        ks    = [s[0] / s[2] for s in samples if s[2] > 0]
        for rv20, _, ap in samples:
            all_rv.append(rv20); all_ap.append(ap)
        per_symbol.append({
            "symbol": sym,
            "rv20_med": round(_median(rv20s), 4),
            "rv60_med": round(_median(rv60s), 4),
            "atr_proxy_med": round(_median(aps), 4),
            "K_med": round(_median(ks), 4),
            "n": len(samples),
        })

    if not per_symbol:
        _log.console("No symbols produced clean samples; aborting.")
        return

    # ── Calibration constant K (pooled) ──
    # median-of-per-symbol K is the robust central estimate; the through-origin least-
    # squares slope is the variance-weighted alternative. They should be close.
    K_sym_median = _median([p["K_med"] for p in per_symbol])
    denom = sum(ap * ap for ap in all_ap)
    K_slope = (sum(rv * ap for rv, ap in zip(all_rv, all_ap, strict=False)) / denom) if denom > 0 else None

    # ── Vol tiers by per-symbol median realized (20d) vol ──
    rv_meds = sorted(p["rv20_med"] for p in per_symbol)
    t1 = _pct(rv_meds, 1 / 3.0)
    t2 = _pct(rv_meds, 2 / 3.0)

    def _tier(rv):
        return "calm" if rv <= t1 else ("mid" if rv <= t2 else "volatile")

    tiers = {}
    for p in per_symbol:
        tiers.setdefault(_tier(p["rv20_med"]), []).append(p)

    tier_rows = []
    flat_prem = calculate_black_scholes_call(BIAS_PRICE, BIAS_STRIKE, BIAS_T, BIAS_RATE, FLAT_SIGMA)
    for name in ("calm", "mid", "volatile"):
        grp = tiers.get(name, [])
        if not grp:
            continue
        rvmed = _median([p["rv20_med"] for p in grp])
        real_prem = calculate_black_scholes_call(BIAS_PRICE, BIAS_STRIKE, BIAS_T, BIAS_RATE, rvmed)
        tier_rows.append({
            "tier": name,
            "n_symbols": len(grp),
            "rv20_median": round(rvmed, 4),
            "over_credit_x": round(FLAT_SIGMA / rvmed, 3) if rvmed > 0 else None,
            "flat_premium": flat_prem,
            "realized_premium": real_prem,
            "premium_bias_x": round(flat_prem / real_prem, 3) if real_prem > 0 else None,
        })

    frac_overcredit = sum(1 for p in per_symbol if p["rv20_med"] < FLAT_SIGMA) / len(per_symbol)
    vol_floor   = round(_pct(rv_meds, 0.05), 2)
    vol_ceiling = round(_pct(rv_meds, 0.95), 2)
    K_reco = round(K_sym_median, 3)

    # ── Report ──
    _log.console("=" * 78)
    _log.console(f"COVERED-CALL IV REALISM — {n_used} symbols, flat placeholder sigma = {FLAT_SIGMA}")
    _log.console("=" * 78)
    _log.console(f"{'tier':<10}{'symbols':>9}{'rv20_med':>10}{'flat/rv':>9}"
                 f"{'flatprem':>10}{'realprem':>10}{'prem_bias':>10}")
    for r in tier_rows:
        _log.console(f"{r['tier']:<10}{r['n_symbols']:>9}{r['rv20_median']:>10.4f}"
                     f"{(r['over_credit_x'] or 0):>9.3f}{r['flat_premium']:>10.2f}"
                     f"{r['realized_premium']:>10.2f}{(r['premium_bias_x'] or 0):>10.3f}")
    _log.console("")
    _log.console(f"  Symbols whose realized vol < flat {FLAT_SIGMA} (flat over-credits): "
                 f"{frac_overcredit*100:.1f}%")
    _log.console(f"  Universe median realized vol (20d): {_median(rv_meds):.4f}")
    _log.console(f"  Calibration K = realized / ATR-proxy:  median-of-symbol = {K_sym_median:.4f} | "
                 f"pooled through-origin slope = {K_slope:.4f}")
    _log.console(f"  ==> recommend IV_ATR_K = {K_reco}, IV_FLOOR = {vol_floor}, "
                 f"IV_CEILING = {vol_ceiling}")

    payload = {
        "meta": {
            "symbols": n_used,
            "flat_sigma": FLAT_SIGMA,
            "rv_windows": [RV_WIN, RV_WIN_LONG],
            "atr_period": ATR_PERIOD,
            "stride": STRIDE,
            "frac_symbols_overcredited": round(frac_overcredit, 4),
            "universe_rv20_median": round(_median(rv_meds), 4),
            "tier_breaks_rv20": [round(t1, 4), round(t2, 4)],
            "calib": {"K_median": round(K_sym_median, 4), "K_slope": round(K_slope, 4)},
            "recommended": {"IV_ATR_K": K_reco, "vol_floor": vol_floor, "vol_ceiling": vol_ceiling},
        },
        "vol_tiers": tier_rows,
        "per_symbol": sorted(per_symbol, key=lambda p: p["rv20_med"]),
    }
    with open(OUT_FILE, "w") as f:
        json.dump(payload, f)
    _log.console(f"\nSaved {len(per_symbol)} per-symbol summaries to {OUT_FILE}")


if __name__ == "__main__":
    run()
