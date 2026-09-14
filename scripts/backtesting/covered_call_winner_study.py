"""
Covered-Call Winner-Cap Study — R&D #26 follow-up, Item 1 (flower over-capping).

execute_weekly_covered_call_pass writes a call on EVERY risk-locked winner
(`is_winner and is_risk_locked and not has_active_call and is_optionable`), capping
exactly the upside CLAUDE.md's Flower-Protection philosophy ("a win that dumps a flower
is still a mistake") exists to protect. This study tests, from history, whether that cap
actually costs money and whether the cost concentrates in the strongest winners — the
signal that justifies excluding high-conviction flowers from call-writing.

Method (pure price/volume, split-adjusted, NO look-ahead):
  - Eligibility proxy at bar i (reconstructable analog of a risk-locked winner):
    trailing 20-day return > 0 AND close >= SMA50.
  - Conviction axis: mom60 = trailing 60-trading-day return %. (L60, the live gate's
    signal, is a Chaikin-scored composite only reconstructable 2022-11+ via the full
    scoring pipeline; mom60 is the price analog that drives whether a winner runs past
    the strike, and spans the full OHLCV history. The CFG L60 ceiling is set as a
    tunable knob seeded from the strong-conviction zone — see the report.)
  - Simulate the weekly write over a 5-trading-day horizon: strike + premium from the
    IMPORTED aether.options.select_covered_call (single source, no re-derivation),
    priced at the SAME per-symbol ATR-IV proxy the engine ships in B1
    (sigma = clamp((ATR/price)*sqrt(252)*IV_ATR_K, IV_FLOOR, IV_CEILING); K/floor/ceiling
    from covered_call_iv_study.py).
  - Per observation:
      held_ret    = (exit - entry) / entry
      covered_ret = (min(exit, strike) - entry + premium) / entry
      edge        = covered_ret - held_ret = (premium - max(0, exit - strike)) / entry
    edge > 0 => the call helped (premium beat forgone upside); edge < 0 => the cap cost
    real money. Bucketing edge by mom60 shows where writing calls destroys winner upside.

Gate (|t| >= 1.96, n >= 20): a high-momentum bucket whose mean edge is significantly
negative confirms the flower over-cap is real and localized -> wire the L60 exclusion.
Null (no significant negative bucket) => ship computed-but-unwired per split-gating.

Usage:
    python scripts/backtesting/covered_call_winner_study.py

Writes Data/covered_call_winner_study.json. Re-run MONTHLY with the OHLCV top-up.
"""

import json
import math
import os
import sys
from pathlib import Path


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import instruments
from aether.options import select_covered_call
from aether.risk_utils import _split_adjust_ohlcv
from aether_logger import get_logger as _get_logger


_log = _get_logger("cc_winner_study")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
OHLCV    = Path(os.environ.get("AETHER_OHLCV_DIR", str(BASE_DIR / "Data" / "Symbol_full")))
OUT_FILE = BASE_DIR / "Data" / "covered_call_winner_study.json"

# Per-symbol ATR-IV proxy — MUST match aether/options.py after B1 (calibrated by
# covered_call_iv_study.py). Kept as literals here so the study is self-contained.
IV_ATR_K   = 0.67
IV_FLOOR   = 0.18
IV_CEILING = 0.64
FLAT_RATE  = 0.04

TRADING_DAYS = 252
HORIZON      = 5      # weekly write ≈ 5 trading days
SMA_WIN      = 50
MOM_WIN      = 60     # trailing-return conviction window
RET_WIN      = 20     # "winner" lookback
ATR_PERIOD   = 14
STRIDE       = 7      # sample ~weekly (writes are weekly) but decorrelate a little
WARMUP       = MOM_WIN + 2
MIN_OBS      = 20     # gate floor per bucket

# mom60 buckets (ascending) — read the breakpoint where mean edge turns negative.
MOM_BINS = [(-1e9, 0.0), (0.0, 10.0), (10.0, 25.0), (25.0, 50.0), (50.0, 1e9)]
MOM_LABELS = ["<=0%", "0-10%", "10-25%", "25-50%", ">50%"]


def _bars(path):
    try:
        with open(path) as f:
            ts = json.load(f).get("Time Series (Daily)", {})
    except Exception:
        return None
    dates = sorted(ts.keys())
    if len(dates) < WARMUP + HORIZON + STRIDE:
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


def _atr_implied_vol(atr, price):
    if not atr or not price or atr <= 0 or price <= 0:
        return 0.30
    return max(IV_FLOOR, min(IV_CEILING, (atr / price) * math.sqrt(TRADING_DAYS) * IV_ATR_K))


def _clean_window(o, h, l, c, v, a, b):
    for i in range(a, b + 1):
        if v[i] <= 0 or (o[i] == h[i] == l[i] == c[i]):
            return False
    return True


def _bucket(mom):
    for (lo, hi), lab in zip(MOM_BINS, MOM_LABELS, strict=False):
        if lo < mom <= hi:
            return lab
    return MOM_LABELS[0]


def _stats(edges):
    n = len(edges)
    if n == 0:
        return None
    mean = sum(edges) / n
    win = sum(1 for e in edges if e > 0) / n
    if n > 1:
        var = sum((e - mean) ** 2 for e in edges) / (n - 1)
        se = math.sqrt(var / n)
        t = mean / se if se > 0 else 0.0
    else:
        t = 0.0
    return {"n": n, "mean_edge_pct": round(mean * 100, 4),
            "t": round(t, 2), "win_rate": round(win, 4)}


def run():
    files = sorted(OHLCV.glob("*_daily.json"))
    _log.console(f"Scanning {len(files)} symbols for winner-cap edge "
                 f"(horizon {HORIZON}d, conviction = trailing-{MOM_WIN}d return)...\n")

    by_bucket = {lab: [] for lab in MOM_LABELS}
    all_edges, all_held, all_covered, all_drag = [], [], [], []
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
        atr = _rolling_atr(h, l, c)
        n = len(c)
        used = False
        for i in range(WARMUP, n - HORIZON, STRIDE):
            if atr[i] is None or c[i] <= 0 or c[i - MOM_WIN] <= 0 or c[i - RET_WIN] <= 0:
                continue
            if not _clean_window(o, h, l, c, v, i - MOM_WIN, i + HORIZON):
                continue
            sma50 = sum(c[i - SMA_WIN + 1:i + 1]) / SMA_WIN
            ret20 = c[i] / c[i - RET_WIN] - 1.0
            # eligibility: risk-locked-winner proxy
            if not (ret20 > 0 and c[i] >= sma50):
                continue
            entry = c[i]
            sigma = _atr_implied_vol(atr[i], entry)
            opt = select_covered_call(sym, entry, atr[i], volatility=sigma, interest_rate=FLAT_RATE)
            strike = opt["strike"]
            premium = opt["premium_price"]
            exit_px = c[i + HORIZON]
            held = (exit_px - entry) / entry
            covered = (min(exit_px, strike) - entry + premium) / entry
            edge = covered - held           # = (premium - max(0, exit-strike))/entry
            mom60 = (c[i] / c[i - MOM_WIN] - 1.0) * 100.0
            by_bucket[_bucket(mom60)].append(edge)
            all_edges.append(edge); all_held.append(held)
            all_covered.append(covered); all_drag.append(held - covered)
            used = True
        if used:
            n_used += 1

    if not all_edges:
        _log.console("No eligible winner observations; aborting.")
        return

    overall = _stats(all_edges)
    bucket_rows = []
    for lab in MOM_LABELS:
        st = _stats(by_bucket[lab])
        if st:
            st["bucket"] = lab
            bucket_rows.append(st)

    # Gate: a high-momentum bucket with mean edge significantly < 0.
    neg_sig = [r for r in bucket_rows
               if r["mean_edge_pct"] < 0 and r["t"] <= -1.96 and r["n"] >= MIN_OBS]
    gate_pass = bool(neg_sig)
    # breakpoint = first ascending bucket whose mean edge is negative (cap starts costing)
    breakpoint_bucket = next((r["bucket"] for r in bucket_rows if r["mean_edge_pct"] < 0), None)

    # ── Report ──
    n_all = len(all_edges)
    _log.console("=" * 82)
    _log.console(f"COVERED-CALL WINNER-CAP — {n_used} symbols, {n_all} eligible-winner writes")
    _log.console("=" * 82)
    _log.console(f"  Overall covered-vs-held edge: {overall['mean_edge_pct']:+.3f}% "
                 f"(t={overall['t']}, win {overall['win_rate']*100:.1f}%, n={overall['n']})")
    _log.console(f"  Mean held_ret {sum(all_held)/n_all*100:+.3f}%  |  "
                 f"covered_ret {sum(all_covered)/n_all*100:+.3f}%  |  "
                 f"mean cap_drag {sum(all_drag)/n_all*100:+.3f}%")
    _log.console("")
    _log.console(f"  {'mom60 bucket':<12}{'n':>8}{'edge%':>10}{'t':>8}{'win%':>8}")
    for r in bucket_rows:
        _log.console(f"  {r['bucket']:<12}{r['n']:>8}{r['mean_edge_pct']:>10.3f}"
                     f"{r['t']:>8.2f}{r['win_rate']*100:>8.1f}")
    _log.console("")
    _log.console(f"  GATE (|t|>=1.96, n>=20, edge<0 in a high-mom bucket): "
                 f"{'PASS' if gate_pass else 'NULL'}")
    if breakpoint_bucket:
        _log.console(f"  Cap starts costing at mom60 bucket: {breakpoint_bucket}")

    # L60 ceiling: momentum!=L60 in units; seed the CFG default in the strong-conviction
    # zone (L60 in [-10,+10], "strong long" ~>= +6). Tunable via AETHER_CC_L60_CEILING.
    l60_ceiling_default = 6.0

    payload = {
        "meta": {
            "symbols": n_used,
            "observations": n_all,
            "horizon_days": HORIZON,
            "conviction": f"trailing_{MOM_WIN}d_return_pct",
            "premium_pricing": {"IV_ATR_K": IV_ATR_K, "IV_FLOOR": IV_FLOOR, "IV_CEILING": IV_CEILING},
            "overall": overall,
            "mean_held_pct": round(sum(all_held) / n_all * 100, 4),
            "mean_covered_pct": round(sum(all_covered) / n_all * 100, 4),
            "mean_cap_drag_pct": round(sum(all_drag) / n_all * 100, 4),
            "gate": {
                "pass": gate_pass,
                "sig_negative_buckets": [r["bucket"] for r in neg_sig],
                "breakpoint_bucket": breakpoint_bucket,
            },
            "recommended": {"l60_ceiling": l60_ceiling_default,
                            "note": "momentum-seeded strong-conviction default; tunable via AETHER_CC_L60_CEILING"},
        },
        "buckets": bucket_rows,
    }
    with open(OUT_FILE, "w") as f:
        json.dump(payload, f)
    _log.console(f"\nSaved bucketed winner-cap stats to {OUT_FILE}")


if __name__ == "__main__":
    run()
