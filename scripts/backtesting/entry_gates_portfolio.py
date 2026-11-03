"""
Entry Gates in the Portfolio Backtest — do the walk-forward rule picks (R&D #57) survive slots,
ranking, stops and costs?

rule_walkforward.py averaged every name a rule admits (~73 a day). The live portfolio holds 5-8,
ranked by S10+L60, with ATR stops, the 1% loss cap, regime cash buffers and costs. This replays
portfolio_backtest.simulate with the buy gates passed through its `admit` hook:

    no_gates        today's portfolio backtest (score threshold only, no Setup/S10/R/R gates)
    current         today's live gates: S10 >= 2, Setup full, R/R >= 2 (elite waiver)
    adaptive        each month the rule rule_walkforward picked from the prior 12 months
                    (months before its first pick use `current`)
    static_in_sample  the single best rule over the whole sample (hindsight, labelled)
    one relaxation at a time from `current`: setup_off, setup_sma, rr_1, s10_floor_0,
    leader_path, rebound_path

Scores and gate inputs come from the rule_walkforward panel (the replay fixed in PR #212), so the
portfolio ranks exactly what the gates saw. Each variant runs from fresh cash on the test window
(TEST_START onwards, the walk-forward's out-of-sample months) and on the full window.

Decision rule (fixed before running), on the test window:
    daily portfolio return, adaptive minus current; Newey-West t, lag 5.
    ADAPTIVE_PORTFOLIO_HELPS  mean > 0 AND HAC t >= 1.96 AND adaptive max drawdown no more than
                              MAX_EXTRA_DD points deeper than current's -> propose an adaptive
                              mode (weekly re-pick, bounded steps, owner approval) as its own PR.
    ADAPTIVE_PORTFOLIO_HURTS  mean < 0 AND HAC t <= -1.96.
    INCONCLUSIVE              otherwise -> keep today's gates.
The one-at-a-time relaxations are reported with the same statistic but are secondary: six
tests, so a single |t| ~ 2 among them is weak evidence.

Also reported per variant: CAGR, total return, max drawdown, Sharpe, exposure, trades, win rate,
and winner runs (+40% in 60 days) the portfolio actually bought within 4 weeks of their start.

Panel selection: scores come from the rule_walkforward panel, whose rows need 20 future bars, a
60-bar history and a snapshot within 10% of the OHLCV close. Every variant therefore sees no scores
in each symbol's last 20 bars (symmetric across variants, but delistings drop out early).

Not simulated (as in portfolio_backtest): overrides, bottom-confirmed buys, breadth downgrade,
scarcity sizing, scale-outs, covered calls, rotation, the adaptive S10 floor. Today's universe.

Usage (from a worktree, reading the real caches):
    AETHER_CACHE_DIR=<main>/Data python scripts/backtesting/entry_gates_portfolio.py \
        --panel <rule_wf_panel.pkl> --wf-report <rule_wf_report.json> [--out FILE]
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
import instruments
import portfolio_backtest as pb
import rule_walkforward as wf
import setup_s10_gate_study as sg
from aether.config import CFG

TEST_START = "2024-01-01"
MAX_EXTRA_DD = 5.0
GATE_T = 1.96
HAC_LAG = 5
CATCH_DAYS = 28


def relaxations() -> dict:
    floor, setup, rr, waived, leader, rebound = wf.CURRENT
    return {"setup_off": (floor, "off", rr, waived, leader, rebound),
            "setup_sma": (floor, "sma", rr, waived, leader, rebound),
            "rr_1": (floor, setup, 1.0, waived, leader, rebound),
            "s10_floor_0": (0.0, setup, rr, waived, leader, rebound),
            "leader_path": (floor, setup, rr, waived, True, rebound),
            "rebound_path": (floor, setup, rr, waived, leader, True)}


def admit_set(a, rule) -> frozenset:
    m = wf.admit_mask(a, rule)
    return frozenset(zip(a["sym"][m].tolist(), a["day"][m].tolist()))


def in_set(admitted: frozenset):
    return lambda sym, day: (sym, day) in admitted


def monthly_admit(sets_by_month: dict, fallback: frozenset):
    def admit(sym, day):
        return (sym, day) in sets_by_month.get(day[:7], fallback)
    return admit


def daily_returns(curve) -> dict:
    eq = [e for _d, e, _i in curve]
    return {curve[k][0]: eq[k] / eq[k - 1] - 1 for k in range(1, len(eq)) if eq[k - 1] > 0}


def diff_test(r_a: dict, r_b: dict) -> dict:
    days = sorted(set(r_a) & set(r_b))
    d = [(r_a[x] - r_b[x]) * 100 for x in days]
    t_hac = sg._t_hac(d, HAC_LAG)
    return {"days": len(d), "mean_daily_pct": round(float(np.mean(d)), 4) if d else None,
            "t_hac": round(t_hac, 2) if t_hac is not None else None}


def verdict(test: dict, dd_adaptive, dd_current) -> str:
    m, t = test.get("mean_daily_pct"), test.get("t_hac")
    if m is None or t is None:
        return "INCONCLUSIVE"
    if m > 0 and t >= GATE_T and dd_adaptive >= dd_current - MAX_EXTRA_DD:
        return "ADAPTIVE_PORTFOLIO_HELPS"
    if m < 0 and t <= -GATE_T:
        return "ADAPTIVE_PORTFOLIO_HURTS"
    return "INCONCLUSIVE"


def winners_bought(trades, starts_by_sym) -> int:
    """Winner runs with a buy of that symbol within CATCH_DAYS of the run's start."""
    got = set()
    for t in trades:
        d = datetime.date.fromisoformat(t["entry_date"])
        for s in starts_by_sym.get(t["symbol"], ()):
            if 0 <= (d - s).days <= CATCH_DAYS:
                got.add((t["symbol"], s))
    return len(got)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", required=True)
    ap.add_argument("--wf-report", required=True)
    ap.add_argument("--cost-bps", type=float, default=10.0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    with open(args.panel, "rb") as f:
        rows = pickle.load(f)
    with open(args.wf_report, encoding="utf-8") as f:
        wf_rep = json.load(f)
    a = wf.to_arrays(rows)
    panel = {}
    for r in rows:
        panel.setdefault(r[1], {})[r[0]] = [r[2], r[4]]
    symbols = sorted(set(panel) | {"SPY"})
    bars = {s: b for s in symbols if (ts := br.load_ohlcv(s)) and (b := pb.bars_from_series(ts))}
    first, last = min(a["day"]), max(a["day"])
    calendar = [d for d in sorted(bars["SPY"]["idx"]) if first <= d <= last]
    test_cal = [d for d in calendar if d >= TEST_START]
    excluded = frozenset(s for s in symbols if instruments.is_excluded(s))

    starts = wf.winner_starts(a)
    starts_by_sym = {}
    for k in starts:
        starts_by_sym.setdefault(a["sym"][k], []).append(datetime.date.fromisoformat(a["day"][k]))
    starts_in_test = sum(1 for k in starts if a["day"][k] >= TEST_START)

    current = admit_set(a, wf.CURRENT)
    by_month = {m: admit_set(a, tuple(rule[k] for k in wf.GRID)) for m, rule in wf_rep["chosen_by_month"].items()}
    static_rule = tuple(wf_rep["static_in_sample"]["rule"][k] for k in wf.GRID)
    variants = {"no_gates": None, "current": in_set(current),
                "adaptive": monthly_admit(by_month, current),
                "static_in_sample": in_set(admit_set(a, static_rule))}
    for name, rule in relaxations().items():
        variants[name] = in_set(admit_set(a, rule))

    out = {"meta": {"run_date": str(datetime.date.today()), "test_start": TEST_START, "calendar": [calendar[0], calendar[-1]],
                    "symbols": len(symbols), "cost_bps": args.cost_bps, "winner_runs_in_test": starts_in_test},
           "test": {}, "full": {}}
    results = {}
    for window, cal in (("test", test_cal), ("full", calendar)):
        for name, adm in variants.items():
            res = pb.simulate(panel, bars, cal, risk_rules=True, cost_bps=args.cost_bps, excluded=excluded,
                              max_trade_risk_pct=CFG.system_max_trade_risk_pct, admit=adm)
            m = pb.metrics(res)
            m["winner_runs_bought"] = winners_bought(res["trades"], starts_by_sym)
            out[window][name] = m
            results[(window, name)] = daily_returns(res["curve"])
    tests = {name: diff_test(results[("test", name)], results[("test", "current")])
             for name in variants if name != "current"}
    out["vs_current_test"] = tests
    out["verdict"] = verdict(tests["adaptive"], out["test"]["adaptive"]["max_drawdown_pct"],
                             out["test"]["current"]["max_drawdown_pct"])
    sg._out(json.dumps({k: out[k] for k in ("meta", "verdict", "vs_current_test")}, indent=1))
    cols = ("cagr_pct", "total_return_pct", "max_drawdown_pct", "sharpe", "avg_exposure_pct", "n_trades",
            "win_rate", "winner_runs_bought")
    for window in ("test", "full"):
        sg._out(f"\n[{window}]  " + "  ".join(cols))
        for name, m in out[window].items():
            sg._out(f"  {name:<17}" + "".join(f"{str(m.get(c)):>12}" for c in cols))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)


if __name__ == "__main__":
    main()
