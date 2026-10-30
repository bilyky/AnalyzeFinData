"""
Rebound Path — should the buy stack also admit 60-day losers whose money flow is not Weak?

R&D #58 (portfolio backtest) showed today's gates plus a "rebound path" (past 60-day return
<= -20% and Chaikin money flow not Weak) beating today's gates on both windows with a shallower
drawdown (test CAGR 16.5% vs 12.5%), but not significantly (HAC t 0.64). That run used the
replay before PR #218, whose seasonality leaked the future into the ranking scores. This re-tests
the path on the fixed replay.

Selection caveat: the path was picked after seeing R&D #58 on this same history, so a pass here is
an in-sample confirmation of a pre-specified rule, not an out-of-sample test. Only data after the
panel's last date can give that.

Definitions (fixed before running):
    REBOUND   past60 <= -20% AND money flow != Weak (the R&D #58 path, unchanged)
    current   today's live gates (rule_walkforward.CURRENT)
    current + rebound path = current OR REBOUND (rule_walkforward.admit_mask, rebound_path=True)

Decision rule (fixed before running):
    Portfolio (primary)  test window TEST_START.., fresh cash, portfolio_backtest.simulate:
                         daily return of current+rebound minus current, Newey-West t lag 5.
    Cohort (must agree)  per date, mean winsorized 20-day return REBOUND minus everyone else,
                         Newey-West t lag 19, all dates.
    REBOUND_HELPS   portfolio mean > 0 AND HAC t >= 1.96 AND max drawdown no more than
                    MAX_EXTRA_DD points deeper AND cohort mean > 0 with HAC t >= 1.96
                    -> propose the path for the live gates (own PR, owner sign-off).
    REBOUND_HURTS   portfolio mean < 0 AND HAC t <= -1.96.
    INCONCLUSIVE    otherwise -> no change.
Reported, not decisive: the cohort at 60 days; thresholds -15% / -25%; without the money-flow
condition; per year; 30%+ loss share; the portfolio on the full window.

Usage (from a worktree, reading the real caches):
    AETHER_CACHE_DIR=<main>/Data python scripts/backtesting/rebound_path_study.py --panel <panel.pkl> [--out FILE]
"""
import argparse
import datetime
import json
import os
import pickle
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))
import backtest_ratings as br
import entry_gates_portfolio as eg
import instruments
import portfolio_backtest as pb
import rule_walkforward as wf
import setup_s10_gate_study as sg
from aether.config import CFG

TEST_START = "2024-01-01"
THRESHOLD = -20.0
MAX_EXTRA_DD = 5.0
GATE_T = 1.96


def rebound_mask(a, threshold=THRESHOLD, need_flow=True) -> np.ndarray:
    m = a["past60"] <= threshold
    return m & ~a["mf_weak"] if need_flow else m


def cohort_diff(a, mask, ret, lag) -> dict:
    """Per date mean(ret | mask) - mean(ret | ~mask), winsorized 1/99%; HAC t across dates."""
    ok = ~np.isnan(ret)
    lo, hi = np.quantile(ret[ok], [0.01, 0.99])
    r = np.clip(ret, lo, hi)
    diffs = []
    for d in np.unique(a["day"][ok]):
        sel = ok & (a["day"] == d)
        x, y = r[sel & mask], r[sel & ~mask]
        if len(x) and len(y):
            diffs.append(float(x.mean() - y.mean()))
    t = sg._t_hac(diffs, lag)
    loss = float(((ret <= -30) & mask & ok).sum() / max(1, (mask & ok).sum()))
    return {"days": len(diffs), "mean_diff": round(float(np.mean(diffs)), 3) if diffs else None,
            "t_hac": round(t, 2) if t is not None else None, "n_cohort": int((mask & ok).sum()),
            "loss30_share": round(loss, 4)}


def verdict(port: dict, cohort: dict, dd_new, dd_cur) -> str:
    m, t = port.get("mean_daily_pct"), port.get("t_hac")
    cm, ct = cohort.get("mean_diff"), cohort.get("t_hac")
    if None in (m, t):
        return "INCONCLUSIVE"
    if (m > 0 and t >= GATE_T and dd_new >= dd_cur - MAX_EXTRA_DD
            and cm is not None and ct is not None and cm > 0 and ct >= GATE_T):
        return "REBOUND_HELPS"
    if m < 0 and t <= -GATE_T:
        return "REBOUND_HURTS"
    return "INCONCLUSIVE"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", required=True)
    ap.add_argument("--cost-bps", type=float, default=10.0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    with open(args.panel, "rb") as f:
        rows = pickle.load(f)
    a = wf.to_arrays(rows)
    F = {name: i for i, name in enumerate(wf.FIELDS)}

    cohort = {"20d": cohort_diff(a, rebound_mask(a), a["f20"], 19),
              "60d": cohort_diff(a, rebound_mask(a), a["f60"], 59),
              "20d_threshold_-15": cohort_diff(a, rebound_mask(a, -15.0), a["f20"], 19),
              "20d_threshold_-25": cohort_diff(a, rebound_mask(a, -25.0), a["f20"], 19),
              "20d_no_flow_condition": cohort_diff(a, rebound_mask(a, need_flow=False), a["f20"], 19)}
    for y in sorted({d[:4] for d in a["day"]}):
        yr = np.char.startswith(a["day"], y)
        cohort[f"20d_{y}"] = cohort_diff({k: v[yr] for k, v in a.items()}, rebound_mask(a)[yr], a["f20"][yr], 19)

    panel = {}
    for r in rows:
        panel.setdefault(r[F["sym"]], {})[r[F["day"]]] = [r[F["s10"]], r[F["l60"]]]
    symbols = sorted(set(panel) | {"SPY"})
    bars = {s: b for s in symbols if (ts := br.load_ohlcv(s)) and (b := pb.bars_from_series(ts))}
    first, last = min(a["day"]), max(a["day"])
    calendar = [d for d in sorted(bars["SPY"]["idx"]) if first <= d <= last]
    excluded = frozenset(s for s in symbols if instruments.is_excluded(s))
    floor, setup, rr, waived, leader, _rebound = wf.CURRENT
    variants = {"current": eg.in_set(eg.admit_set(a, wf.CURRENT)),
                "current_plus_rebound": eg.in_set(eg.admit_set(a, (floor, setup, rr, waived, leader, True)))}
    port, daily = {}, {}
    for window, cal in (("test", [d for d in calendar if d >= TEST_START]), ("full", calendar)):
        port[window] = {}
        for name, adm in variants.items():
            res = pb.simulate(panel, bars, cal, risk_rules=True, cost_bps=args.cost_bps, excluded=excluded,
                              max_trade_risk_pct=CFG.system_max_trade_risk_pct, admit=adm)
            port[window][name] = pb.metrics(res)
            daily[(window, name)] = eg.daily_returns(res["curve"])
    port_test = eg.diff_test(daily[("test", "current_plus_rebound")], daily[("test", "current")])
    out = {"meta": {"run_date": str(datetime.date.today()), "test_start": TEST_START, "threshold": THRESHOLD,
                    "calendar": [calendar[0], calendar[-1]], "selection": "in-sample confirmation (see docstring)"},
           "cohort": cohort, "portfolio": port, "portfolio_test_diff": port_test,
           "verdict": verdict(port_test, cohort["20d"], port["test"]["current_plus_rebound"]["max_drawdown_pct"],
                              port["test"]["current"]["max_drawdown_pct"])}
    sg._out(json.dumps(out, indent=1))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)


if __name__ == "__main__":
    main()
