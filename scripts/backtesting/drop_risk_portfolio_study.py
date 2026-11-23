"""
Drop-Risk Portfolio Study (R&D #117) - does avoiding or downsizing drop-prone buys help the
PORTFOLIO once stops, sizing and slots are in, and is it just volatility?

Why: R&D #115 (tail_risk_study.py, PR #304) found that among the buys the live gates admit,
stocks with >= 4 days of a <= -10% close-to-close drop in the prior 756 sessions (HIGH,
panel field drops756) hit a -15% worst close within 10 sessions 8.9% of the time vs 1.2% for
0-1 such days (HAC t 8.89, both halves). That measured single trades with no stops, sizing or
slots, and allowed only a proposal. This study replays the portfolio.

Pre-registered rule (committed and pushed before the first full run; not changed after):

Engine: portfolio_backtest.simulate on the rule_walkforward panel, set up exactly as R&D #75
(prod_review_rules_study.py part A): today's live entry gates through `admit`
(rule_walkforward.admit_mask(a, CURRENT)), the LIVE profile (SPY-L60 band plus the SPY-RSP
breadth downgrade, prod_review_rules_study.live_profiles), risk rules on, the live per-trade
loss cap (CFG.system_max_trade_risk_pct), 10 bps a side, instruments.is_excluded names skipped.
Windows: test = TEST_START (2024-01-01, first session 2024-01-02) to the end of the panel;
the full window is reported too. Each variant runs from fresh cash in each window.

Variants (only the entry admission or the size changes; everything else identical):
  current    today's rules.
  skip_high  do not buy when drops756 >= 4 (NaN = short history = not HIGH).
  half_high  buy HIGH names at half the normal size: simulate's size_fn returns 0.5 for a HIGH
             (sym, day), 1.0 otherwise (a multiple of the per-name cap; slots and the loss-cap
             trim unchanged).
  vol_cap    control: do not buy when the stock's 60-session realized daily-return stdev is in
             the top 19% of that day's admitted candidates. Vol = sample stdev (ddof 1) of the
             60 daily returns ending at the signal day's close, from portfolio_backtest's
             split-adjusted closes with placeholder bars dropped (data known at the decision;
             the buy fills next open); NaN with fewer than 60 returns (never capped). A row is
             in the top 19% when the share of that day's admitted rows with vol >= its own is
             <= 0.19 (the denominator counts every admitted row that day). 19% ~ the HIGH share
             of admitted buys in #115 (3,644 of 19,057).

Primary statistics, each variant vs current, on the test window:
  (a) max drawdown difference, percentage points = dd(variant) - dd(current) (drawdowns are
      negative, so positive = shallower). Halves: the test calendar split at the calendar
      midpoint of its first..last session; in each half the max drawdown of that half of the
      one test-window equity curve (peak reset at the half's start), same difference.
  (b) daily portfolio return difference (variant - current): mean (%) and Newey-West HAC t,
      lag 5 (entry_gates_portfolio.diff_test, as R&D #75).
  (c) stop-out tail: portfolio_backtest.stop_tail (P95 and worst loss per stopped trade, % of
      equity).

Verdict per variant (skip_high, half_high):
  LOSS_CUT      dd difference >= 2.0 points AND HAC t of the daily-return difference >= -1.0
                (not clearly worse) AND the dd difference is > 0 in both halves.
  INCONCLUSIVE  otherwise.
  VOL_EXPLAINS  label added to a LOSS_CUT variant when vol_cap also meets the LOSS_CUT
                conditions with a dd difference at least as large as the variant's.
A LOSS_CUT only justifies PROPOSING a live change in a later, separate PR (risk rules apply).
INCONCLUSIVE keeps the rules.

Also reported (not decisive): CAGR, total return, Sharpe, exposure, trades, win rate, the
R&D #57-style winner runs (+40% in 60 days; rule_walkforward.winner_starts) each variant bought
within 4 weeks of the run's start (entry_gates_portfolio.winners_bought), the HIGH and vol-top
shares of admitted rows and their overlap, and the full-window statistics.

Not simulated (as R&D #75): overrides, bottom-confirmed buys, the R&D #13 Bearish-PGR reject
(admit_mask does not model it, as in #75), the cash-deployment upgrade, scarcity sizing,
scale-outs, covered calls, rotation, the adaptive S10 floor; simulated stops fill intraday.
Biases: today's universe (survivorship); ~20% of Chaikin snapshots carry the wrong date's data
(R&D #74), which affects the scores and gates, not drops756 or vol; the 2026-09-01..10-30
factor gap; rows need 20 future bars (each symbol's last 20 bars carry no scores); one
simulated path (R&D #54b: one path is fragile evidence).

Usage (read-only on the real caches; keep the panel cache outside Data/):
    python scripts/backtesting/drop_risk_portfolio_study.py --data-dir <main>/Data \\
        --panel <tmp>/drop_panel.pkl [--rebuild] [--workers N] [--out <main>/Data/drop_risk_portfolio_study_<date>.json]
"""
import argparse
import datetime
import json
import math
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))
import entry_gates_portfolio as egp
import instruments
import portfolio_backtest as pb
import prod_review_rules_study as prr
import rule_walkforward as wf
import setup_s10_gate_study as sg
from aether.config import CFG

TEST_START = "2024-01-01"
HIGH_H = 4
HALF_SIZE = 0.5
VOL_WINDOW = 60
VOL_TOP_SHARE = 0.19
MIN_DD_GAIN = 2.0
MIN_T = -1.0
VARIANTS = ("current", "skip_high", "half_high", "vol_cap")
JUDGED = ("skip_high", "half_high")


# ── Admission and size ───────────────────────────────────────────────────────

def high_mask(drops) -> np.ndarray:
    """HIGH = drops756 >= HIGH_H; NaN (short history) is not HIGH."""
    h = np.asarray(drops, dtype=float)
    return np.isfinite(h) & (h >= HIGH_H)


def realized_vol(closes, i, window=VOL_WINDOW):
    """Sample stdev of the `window` daily returns ending at closes[i]; None when too short."""
    if i < window or i >= len(closes):
        return None
    c = np.asarray(closes[i - window: i + 1], dtype=float)
    if (c[:-1] <= 0).any():
        return None
    return float(np.std(c[1:] / c[:-1] - 1, ddof=1))


def row_vols(syms, days, bars) -> np.ndarray:
    """realized_vol for each (sym, day) row from portfolio_backtest bars; NaN when unknown."""
    out = np.full(len(syms), np.nan)
    for k, (s, d) in enumerate(zip(syms, days)):
        b, i = pb._bar(bars, s, d)
        v = realized_vol(b["c"], i) if b else None
        if v is not None:
            out[k] = v
    return out


def vol_top_mask(days, vol, admitted, share=VOL_TOP_SHARE) -> np.ndarray:
    """Admitted rows in the top `share` of their day's admitted rows by vol.

    A row is in the top when (admitted rows that day with vol >= its vol) / (admitted rows that
    day) <= share. NaN vol is never in the top but counts in the denominator."""
    days = np.asarray(days).astype(str)
    vol = np.asarray(vol, dtype=float)
    out = np.zeros(len(days), dtype=bool)
    idx = np.where(admitted)[0]
    by_day = {}
    for k in idx:
        by_day.setdefault(days[k], []).append(k)
    for ks in by_day.values():
        v = vol[ks]
        n = len(ks)
        for k, x in zip(ks, v):
            if np.isfinite(x) and np.sum(np.isfinite(v) & (v >= x)) / n <= share:
                out[k] = True
    return out


def variant_masks(admitted, high, vol_top) -> dict:
    """Admit mask per variant. half_high admits like current; its size differs (size_fn)."""
    return {"current": admitted, "skip_high": admitted & ~high,
            "half_high": admitted, "vol_cap": admitted & ~vol_top}


def half_size_fn(high_set: frozenset, half=HALF_SIZE):
    return lambda sym, day: half if (sym, day) in high_set else 1.0


# ── Drawdown ─────────────────────────────────────────────────────────────────

def max_drawdown_pct(eq) -> float | None:
    eq = np.asarray(eq, dtype=float)
    if len(eq) < 2:
        return None
    return float(np.min(eq / np.maximum.accumulate(eq) - 1) * 100)


def half_split(days) -> str:
    """Calendar midpoint of the first..last day (ISO); days <= it are the first half."""
    d0, d1 = datetime.date.fromisoformat(days[0]), datetime.date.fromisoformat(days[-1])
    return (d0 + (d1 - d0) / 2).isoformat()


def half_drawdowns(curve, mid: str) -> tuple:
    """(max drawdown % of the first half, of the second half) of one curve [(day, equity, _)]."""
    first = [e for d, e, _i in curve if d <= mid]
    second = [e for d, e, _i in curve if d > mid]
    return max_drawdown_pct(first), max_drawdown_pct(second)


def dd_diff(dd_variant, dd_current):
    """Positive = the variant's drawdown is shallower (drawdowns are negative %)."""
    if dd_variant is None or dd_current is None:
        return None
    return dd_variant - dd_current


# ── Verdict ──────────────────────────────────────────────────────────────────

def meets_loss_cut(dd_gain, t_hac, half_gains) -> bool:
    if dd_gain is None or t_hac is None or None in half_gains:
        return False
    return dd_gain >= MIN_DD_GAIN and t_hac >= MIN_T and all(g > 0 for g in half_gains)


def verdicts(stats: dict) -> dict:
    """stats: {variant: {"dd_gain", "t_hac", "half_gains"}} incl. vol_cap -> {variant: verdict}."""
    vol = stats["vol_cap"]
    vol_ok = meets_loss_cut(vol["dd_gain"], vol["t_hac"], vol["half_gains"])
    out = {}
    for v in JUDGED:
        s = stats[v]
        if not meets_loss_cut(s["dd_gain"], s["t_hac"], s["half_gains"]):
            out[v] = "INCONCLUSIVE"
        elif vol_ok and vol["dd_gain"] >= s["dd_gain"]:
            out[v] = "LOSS_CUT+VOL_EXPLAINS"
        else:
            out[v] = "LOSS_CUT"
    return out


# ── Run ──────────────────────────────────────────────────────────────────────

def _r(x, k=3):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(x, k)


def window_stats(results, cal) -> dict:
    """Per variant vs current on one window: dd diff (+ halves), daily-return test, stop tail."""
    mid = half_split(cal)
    daily = {v: egp.daily_returns(results[v]["curve"]) for v in VARIANTS}
    dd = {v: max_drawdown_pct([e for _d, e, _i in results[v]["curve"]]) for v in VARIANTS}
    halves = {v: half_drawdowns(results[v]["curve"], mid) for v in VARIANTS}
    out = {"half_split": mid, "max_drawdown_pct": {v: _r(dd[v]) for v in VARIANTS},
           "half_max_drawdown_pct": {v: [_r(x) for x in halves[v]] for v in VARIANTS}, "vs_current": {}}
    for v in VARIANTS[1:]:
        test = egp.diff_test(daily[v], daily["current"])
        out["vs_current"][v] = {"dd_gain": _r(dd_diff(dd[v], dd["current"])),
                                "half_gains": [_r(dd_diff(a, b)) for a, b in zip(halves[v], halves["current"])],
                                "mean_daily_diff_pct": test["mean_daily_pct"], "t_hac": test["t_hac"],
                                "days": test["days"]}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, help="the Data dir holding Symbol/ and Symbol_full/ (read-only)")
    ap.add_argument("--panel", required=True, help="pickle cache of the panel (keep it outside Data/)")
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--cost-bps", type=float, default=10.0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    rows = wf.build_panel(args.data_dir, args.panel, args.rebuild, args.workers)
    a = wf.to_arrays(rows)
    if "drops756" not in a:
        raise SystemExit("panel lacks the tail fields (built before them): pass --rebuild")
    panel = {}
    for r in rows:
        panel.setdefault(r[1], {})[r[0]] = [r[2], r[4]]
    symbols = sorted(set(panel) | {"SPY"})
    ohlcv_dir = os.path.join(args.data_dir, "Symbol_full")
    raw = {s: sg._load(ohlcv_dir, s) for s in set(symbols) | {"RSP"}}
    bars = {s: b for s in symbols if raw.get(s) and (b := pb.bars_from_series(raw[s]))}
    first, last = min(a["day"]), max(a["day"])
    calendar = [d for d in sorted(bars["SPY"]["idx"]) if first <= d <= last]
    test_cal = [d for d in calendar if d >= TEST_START]
    excluded = frozenset(s for s in symbols if instruments.is_excluded(s))
    profiles = prr.live_profiles(calendar, panel.get("SPY", {}), raw["SPY"], raw["RSP"])

    admitted = wf.admit_mask(a, wf.CURRENT)
    high = high_mask(a["drops756"])
    vol = row_vols(a["sym"], a["day"], bars)
    vol_top = vol_top_mask(a["day"], vol, admitted)
    masks = variant_masks(admitted, high, vol_top)
    admits = {v: egp.in_set(prr.as_set(a, m)) for v, m in masks.items()}
    high_set = prr.as_set(a, admitted & high)
    sizes = {"half_high": half_size_fn(high_set)}

    starts = wf.winner_starts(a)
    starts_by_sym = {}
    for k in starts:
        starts_by_sym.setdefault(a["sym"][k], []).append(datetime.date.fromisoformat(a["day"][k]))
    n_adm = int(admitted.sum())
    out = {"meta": {"run_date": str(datetime.date.today()), "test_start": TEST_START,
                    "calendar": [calendar[0], calendar[-1]], "test_calendar": [test_cal[0], test_cal[-1]],
                    "symbols": len(symbols), "cost_bps": args.cost_bps, "panel_obs": len(rows),
                    "max_trade_risk_pct": CFG.system_max_trade_risk_pct,
                    "script": "scripts/backtesting/drop_risk_portfolio_study.py",
                    "rule": {"high_h": HIGH_H, "half_size": HALF_SIZE, "vol_window": VOL_WINDOW,
                             "vol_top_share": VOL_TOP_SHARE, "min_dd_gain": MIN_DD_GAIN, "min_t": MIN_T,
                             "hac_lag": egp.HAC_LAG},
                    "admitted_obs": n_adm,
                    "admitted_high": int((admitted & high).sum()),
                    "admitted_no_history": int((admitted & ~np.isfinite(a["drops756"].astype(float))).sum()),
                    "admitted_vol_top": int(vol_top.sum()),
                    "admitted_vol_nan": int((admitted & ~np.isfinite(vol)).sum()),
                    "high_and_vol_top": int((admitted & high & vol_top).sum()),
                    "admitted_obs_test": int((admitted & (a["day"] >= TEST_START)).sum()),
                    "winner_runs_in_test": int(sum(1 for k in starts if a["day"][k] >= TEST_START)),
                    "winner_runs_full": len(starts)},
           "test": {}, "full": {}}
    for window, cal in (("test", test_cal), ("full", calendar)):
        res = {}
        for v in VARIANTS:
            res[v] = pb.simulate(panel, bars, cal, risk_rules=True, cost_bps=args.cost_bps, excluded=excluded,
                                 max_trade_risk_pct=CFG.system_max_trade_risk_pct, admit=admits[v],
                                 profile_fn=profiles.get, size_fn=sizes.get(v))
        stats = window_stats(res, cal)
        stats["metrics"] = {}
        stats["stop_tail"] = {}
        for v in VARIANTS:
            m = pb.metrics(res[v])
            m["winner_runs_bought"] = egp.winners_bought(res[v]["trades"], starts_by_sym)
            stats["metrics"][v] = m
            stats["stop_tail"][v] = pb.stop_tail(res[v]["trades"], CFG.system_max_trade_risk_pct)
        out[window] = stats
        out[window]["trades"] = {v: res[v]["trades"] for v in VARIANTS}
    out["verdict"] = verdicts(out["test"]["vs_current"])
    sg._out(json.dumps({"meta": out["meta"], "verdict": out["verdict"],
                        "test_vs_current": out["test"]["vs_current"]}, indent=1))
    cols = ("cagr_pct", "total_return_pct", "max_drawdown_pct", "sharpe", "avg_exposure_pct", "n_trades",
            "win_rate", "winner_runs_bought")
    for window in ("test", "full"):
        sg._out(f"\n[{window}]  " + "  ".join(cols))
        for v, m in out[window]["metrics"].items():
            sg._out(f"  {v:<10}" + "".join(f"{str(m.get(c)):>12}" for c in cols))
        for v, t in out[window]["stop_tail"].items():
            sg._out(f"  stop tail {v:<10} n {t.get('n_stop_exits')}  P95 {t.get('loss_p95_pct')}  "
                    f"worst {t.get('loss_worst_pct')}")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)


if __name__ == "__main__":
    main()
