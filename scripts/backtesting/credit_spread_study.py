"""
Credit-Spread Study — Rule C of the Defensive Risk-Management Overlay (backtest gate).

`plans/options-challenge-strategy.md` proposes selling out-of-the-money vertical
credit spreads (bull-put in an up-regime, bear-call in a down-regime) as a high-win-rate
income structure. High win-rate is NOT the same as positive expectancy — one max-loss can
erase many small credits — so before the adviser learns to *build* these spreads
(`aether/options_adviser.py`), this study asks, from 25+ years x 500 symbols of history:

    1. Do OTM vertical credit spreads expire OTM often enough to matter (win-rate)?
    2. And is the EXPECTANCY (return on capital-at-risk) significantly positive once the
       occasional max-loss is paid? (the gate — win-rate alone is a trap.)
    3. Where does the edge concentrate — regime, OTM distance, DTE, index vs single-name?

Method (PURE price/volume, split-adjusted, entry-anchored, NO look-ahead):
  - At each entry bar i (stride-sampled), regime = c[i] >= SMA50 -> bull-put (sell an OTM
    put below spot), else -> bear-call (sell an OTM call above spot).
  - Volatility = trailing realized vol from closes[:i+1] (aether.option_pricing.realized_vol),
    the same no-look-ahead IV proxy the replay study uses. Modeled RV understates traded IV
    (the vol-risk-premium, IV ~ 1.1-1.3x RV) so modeled CREDITS are UNDERSTATED -> this study
    is conservative for a credit SELLER (real premium received would be higher).
  - Price the two legs with aether.option_pricing.bs_price at T = horizon/252 years; short leg
    filled at modeled BID, long leg at modeled ASK (the adviser's conservative fill convention),
    using aether.option_pricing.DEFAULT_SPREAD_PCT for the half-spread. Net credit = bid_short - ask_long.
  - At expiry (c[i + horizon]) settle the vertical; capital-at-risk = width - credit, and the
    per-episode outcome is return-on-risk = pnl / max_loss (the natural credit-spread expectancy unit).

Gate (|t| >= 1.96, n >= 20 on the best regime/OTM/DTE combo, mean return-on-risk > 0): a
significantly positive expectancy confirms the structure and unlocks C2 (the builders). A null
parks Rule C with a documented result (like the Gann Sq9 study), no builders shipped.

Cadence: re-derive MONTHLY with the OHLCV top-up (the winning combo comes from decades of data
and does not move week-to-week); weekly re-runs only overfit.

Usage:
    python scripts/backtesting/credit_spread_study.py

Writes Data/credit_spread_study.json (meta.gate + per-combo rows + index-proxy spotlight).
"""

import json
import math
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import instruments
from aether.option_pricing import DEFAULT_RATE, DEFAULT_SPREAD_PCT, bs_price, realized_vol
from aether.risk_utils import _split_adjust_ohlcv
from aether_logger import get_logger as _get_logger

_log = _get_logger("credit_spread_study")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
OHLCV    = Path(os.environ.get("AETHER_OHLCV_DIR", str(BASE_DIR / "Data" / "Symbol_full")))
OUT_FILE = BASE_DIR / "Data" / "credit_spread_study.json"

# ── Structure parameters (swept by bucketing so one pass yields every cell) ──
SMA_WIN    = 50            # regime filter: close vs SMA50 picks bull-put vs bear-call
VOL_WIN    = 21            # trailing realized-vol window (~1 option-holding month)
STRIDE     = 5             # decorrelate overlapping DTE windows (entries are ~weekly)
MIN_SPOT   = 5.0           # skip sub-$5 names where the $0.02 spread floor dominates
RATE       = DEFAULT_RATE  # flat risk-free rate (single-sourced from option_pricing)
SPREAD_PCT = DEFAULT_SPREAD_PCT  # modeled half bid/ask spread as a fraction of mid

# Sweep grids — read the cell where expectancy turns significantly positive.
OTM_GRID   = [0.10, 0.15]  # short strike this far OTM from spot
WIDTH_GRID = [0.05]        # long strike this much further OTM (spread width, % of spot)
DTE_GRID   = [21, 42]      # holding horizon in TRADING days (~30 / ~60 calendar)
TRADING_DAYS = 252

WARMUP     = max(SMA_WIN, VOL_WIN) + 2
MIN_OBS    = 20            # gate floor per combo
INDEX_PROXIES = {"SPY", "QQQ", "IWM", "DIA"}  # liquid proxies for the "high win-rate" claim

# ── Alpha gate ──
GATE_T = 1.96
GATE_N = 20


def _bars(path):
    """Return split-adjusted parallel float arrays (open, high, low, close, volume) or None."""
    try:
        with open(path) as f:
            ts = json.load(f).get("Time Series (Daily)", {})
    except Exception:
        return None
    dates = sorted(ts.keys())
    if len(dates) < WARMUP + max(DTE_GRID) + STRIDE:
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


def _spread_outcome(kind, spot, sigma, horizon, otm, width_pct):
    """Model one OTM vertical credit spread entered at `spot` and settled `horizon`
    trading days later. Returns a dict of raw economics, or None if the modeled spread is
    not a valid net credit (both legs same strike, non-positive credit, or credit >= width).

    kind: 'bull_put' (sell OTM put below spot) or 'bear_call' (sell OTM call above spot).
    Settlement is done by the caller against the realized exit price (no look-ahead here).
    """
    T = horizon / TRADING_DAYS
    if kind == "bull_put":
        k_short = round(spot * (1 - otm), 2)               # OTM put below spot
        k_long  = round(spot * (1 - otm - width_pct), 2)   # further OTM (protection)
        otype = "PUT"
    else:  # bear_call
        k_short = round(spot * (1 + otm), 2)               # OTM call above spot
        k_long  = round(spot * (1 + otm + width_pct), 2)   # further OTM (protection)
        otype = "CALL"
    if k_long <= 0 or k_short == k_long:
        return None
    mid_short = bs_price(spot, k_short, T, RATE, sigma, otype)
    mid_long  = bs_price(spot, k_long,  T, RATE, sigma, otype)
    bid_short = max(0.0, mid_short - max(0.02, round(SPREAD_PCT * mid_short, 2)))  # sell at bid
    ask_long  = mid_long + max(0.02, round(SPREAD_PCT * mid_long, 2))             # buy at ask
    credit = bid_short - ask_long
    width = abs(k_short - k_long)
    if credit <= 0 or credit >= width:      # not a genuine credit spread under the model
        return None
    return {"kind": kind, "k_short": k_short, "k_long": k_long,
            "credit": credit, "width": width, "otype": otype}


def _settle(spread, exit_px):
    """Return (pnl_per_share, ror, full_otm) for a modeled spread at expiry price exit_px.

    ror = pnl / max_loss = return on capital-at-risk (the credit-spread expectancy unit).
    full_otm = short leg expired worthless (the 'expires OTM' win the premise leans on).
    """
    k_s, k_l, credit, width = spread["k_short"], spread["k_long"], spread["credit"], spread["width"]
    if spread["kind"] == "bull_put":
        short_liab = max(k_s - exit_px, 0.0)
        long_val   = max(k_l - exit_px, 0.0)
        full_otm = exit_px >= k_s
    else:  # bear_call
        short_liab = max(exit_px - k_s, 0.0)
        long_val   = max(exit_px - k_l, 0.0)
        full_otm = exit_px <= k_s
    pnl = credit - (short_liab - long_val)     # long leg caps the loss at (width - credit)
    max_loss = width - credit
    ror = pnl / max_loss if max_loss > 0 else 0.0
    return pnl, ror, full_otm


def _stats(records):
    """Aggregate episode records into win-rate / full-OTM-rate / expectancy stats."""
    n = len(records)
    if n == 0:
        return None
    rors = [r["ror"] for r in records]
    mean = sum(rors) / n
    win = sum(1 for r in records if r["pnl"] > 0) / n
    full_otm = sum(1 for r in records if r["full_otm"]) / n
    mean_credit_pct = sum(r["credit"] / r["width"] for r in records) / n
    if n > 1:
        var = sum((x - mean) ** 2 for x in rors) / (n - 1)
        sd = math.sqrt(var)
        t = mean / (sd / math.sqrt(n)) if sd > 0 else 0.0
    else:
        t = 0.0
    return {"n": n,
            "win_rate": round(win, 4),
            "full_otm_rate": round(full_otm, 4),
            "mean_ror": round(mean, 4),        # expectancy in units of capital-at-risk
            "t": round(t, 2),
            "mean_credit_pct_of_width": round(mean_credit_pct, 4)}


def run():
    files = sorted(OHLCV.glob("*_daily.json"))
    _log.console(f"Scanning {len(files)} symbols for OTM vertical credit-spread expectancy "
                 f"(horizons {DTE_GRID}d, OTM {OTM_GRID}, width {WIDTH_GRID})...\n")

    # by (kind, otm, dte) -> list of episode records; plus an index-proxy-only mirror
    by_combo = defaultdict(list)
    by_combo_index = defaultdict(list)
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
        o, h, l, c, v = arrays
        n = len(c)
        is_index = sym in INDEX_PROXIES
        used = False
        for i in range(WARMUP, n - max(DTE_GRID), STRIDE):
            spot = c[i]
            if spot < MIN_SPOT or c[i - SMA_WIN] <= 0:
                continue
            sigma = realized_vol(c[:i + 1], window=VOL_WIN)
            if not sigma or sigma <= 0:
                continue
            sma50 = sum(c[i - SMA_WIN + 1:i + 1]) / SMA_WIN
            kind = "bull_put" if spot >= sma50 else "bear_call"
            for otm in OTM_GRID:
                for width_pct in WIDTH_GRID:
                    spread = _spread_outcome(kind, spot, sigma, min(DTE_GRID), otm, width_pct)
                    if spread is None:
                        continue
                    for dte in DTE_GRID:
                        # re-price at this DTE (T scales the premium) then settle at c[i+dte]
                        s = _spread_outcome(kind, spot, sigma, dte, otm, width_pct)
                        if s is None:
                            continue
                        exit_px = c[i + dte]
                        if exit_px <= 0:
                            continue
                        pnl, ror, full_otm = _settle(s, exit_px)
                        rec = {"pnl": pnl, "ror": ror, "full_otm": full_otm,
                               "credit": s["credit"], "width": s["width"]}
                        key = (kind, otm, dte)
                        by_combo[key].append(rec)
                        if is_index:
                            by_combo_index[key].append(rec)
                        used = True
        if used:
            n_used += 1

    if not by_combo:
        _log.console("No eligible credit-spread episodes; aborting.")
        return False

    # ── Per-combo table ──
    combo_rows = []
    for key in sorted(by_combo):
        kind, otm, dte = key
        st = _stats(by_combo[key])
        if not st:
            continue
        st.update({"kind": kind, "otm": otm, "dte": dte})
        combo_rows.append(st)

    # ── Gate: best combo (n>=MIN_OBS, expectancy>0) by expectancy t-stat ──
    eligible = [r for r in combo_rows if r["n"] >= MIN_OBS and r["mean_ror"] > 0]
    eligible.sort(key=lambda r: r["t"], reverse=True)
    best = eligible[0] if eligible else None
    passes = bool(best) and best["t"] >= GATE_T and best["n"] >= GATE_N

    all_recs = [r for recs in by_combo.values() for r in recs]
    overall = _stats(all_recs)

    # ── Report ──
    n_all = len(all_recs)
    _log.console("=" * 92)
    _log.console(f"CREDIT-SPREAD EXPECTANCY — {n_used} symbols, {n_all} modeled episodes")
    _log.console("=" * 92)
    _log.console(f"  Overall: win {overall['win_rate']*100:.1f}%  full-OTM {overall['full_otm_rate']*100:.1f}%  "
                 f"E[ror] {overall['mean_ror']:+.4f}  t={overall['t']}  "
                 f"credit/width {overall['mean_credit_pct_of_width']*100:.1f}%")
    _log.console("")
    _log.console(f"  {'kind':<10}{'otm':>6}{'dte':>5}{'n':>7}{'win%':>8}{'fullOTM%':>10}"
                 f"{'E[ror]':>9}{'t':>7}{'cred/w%':>9}")
    for r in combo_rows:
        _log.console(f"  {r['kind']:<10}{r['otm']:>6.2f}{r['dte']:>5}{r['n']:>7}"
                     f"{r['win_rate']*100:>8.1f}{r['full_otm_rate']*100:>10.1f}"
                     f"{r['mean_ror']:>9.4f}{r['t']:>7.2f}{r['mean_credit_pct_of_width']*100:>9.1f}")

    # ── Index-proxy spotlight (the doc's "high win rate on liquid index" claim) ──
    index_rows = []
    for key in sorted(by_combo_index):
        kind, otm, dte = key
        st = _stats(by_combo_index[key])
        if not st or st["n"] < 5:
            continue
        st.update({"kind": kind, "otm": otm, "dte": dte})
        index_rows.append(st)
    if index_rows:
        _log.console("")
        _log.console(f"  INDEX PROXIES ({', '.join(sorted(INDEX_PROXIES))}) — win-rate spotlight:")
        for r in index_rows:
            _log.console(f"    {r['kind']:<10}{r['otm']:>6.2f}{r['dte']:>5}{r['n']:>7}"
                         f"{r['win_rate']*100:>8.1f}{r['mean_ror']:>9.4f}{r['t']:>7.2f}")

    # ── Gate verdict ──
    _log.console("")
    _log.console("=" * 92)
    _log.console(f"  GATE (|t|>=1.96, n>=20, E[ror]>0 on best combo): {'PASS' if passes else 'FAIL'}")
    if best:
        _log.console(f"  best: {best['kind']} otm={best['otm']:.2f} dte={best['dte']}  "
                     f"win {best['win_rate']*100:.1f}%  E[ror] {best['mean_ror']:+.4f}  "
                     f"t={best['t']} (n={best['n']})")
    else:
        _log.console("  No combo has n>=20 with positive expectancy.")
    _log.console(f"  ==> {'PASS — proceed to C2 (build the spread builders)' if passes else 'FAIL — park Rule C with a documented null'}")

    # ── Persist ──
    payload = {
        "meta": {
            "symbols": n_used,
            "episodes": n_all,
            "horizons_trading_days": DTE_GRID,
            "otm_grid": OTM_GRID,
            "width_grid": WIDTH_GRID,
            "pricing": {"rate": RATE, "spread_pct": SPREAD_PCT, "vol_window": VOL_WIN,
                        "note": "modeled RV understates traded IV -> credits conservative for a seller"},
            "overall": overall,
            "gate": {
                "pass": passes,
                "threshold": {"t": GATE_T, "n": GATE_N},
                "best_combo": best,
            },
        },
        "combos": combo_rows,
        "index_proxies": index_rows,
    }
    with open(OUT_FILE, "w") as f:
        json.dump(payload, f)
    _log.console(f"\nSaved credit-spread expectancy stats to {OUT_FILE}")
    return passes


if __name__ == "__main__":
    run()
