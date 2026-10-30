"""
Entry Ranking Study — what should decide which candidates take the portfolio's few slots?

R&D #58 found the buy gates barely change what a 3-6 slot portfolio holds: the names ranked
first by S10 + L60 already clear them. What the portfolio holds is decided by the ranking. This
compares six rankings, all under today's live gates (S10 >= 2, Setup, R/R >= 2 with the elite
waiver), in portfolio_backtest.simulate (slots, regime cash, ATR stops, 1% loss cap, costs):

    combined          S10 + L60 (today)
    s10               Short10 only
    l60               Long60 only
    combined_waived   S10 + L60 with the R&D #34 breakout waiver always on
    deepest_pullback  most negative 60-day return first
    leader_first      Chaikin all-Strong leaders first (R&D #55), then S10 + L60

Decision rule (fixed before running):
    TRAIN  calendar days before SPLIT; the ranking with the best TRAIN Sharpe is picked.
    TEST   SPLIT onwards, from fresh cash, never seen by the pick.
    RANKING_HELPS  the pick is not `combined` AND on TEST its daily return minus `combined`'s has
                   mean > 0 with Newey-West t (lag 5) >= 1.96 AND its max drawdown is no more than
                   MAX_EXTRA_DD points deeper -> propose the ranking change as its own PR.
    KEEP_CURRENT   the pick is `combined`.
    INCONCLUSIVE   otherwise.
All six are reported on both windows; only the pick is tested, so there is one decisive test.

Inputs: the rule_walkforward panel (scores from the replay fixed in PR #212). Not simulated: as
in portfolio_backtest. Today's universe.

Usage (from a worktree, reading the real caches):
    AETHER_CACHE_DIR=<main>/Data python scripts/backtesting/ranking_study.py --panel <rule_wf_panel.pkl> [--out FILE]
"""
import argparse
import datetime
import json
import os
import pickle
import sys

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

SPLIT = "2025-01-01"
MAX_EXTRA_DD = 5.0
GATE_T = 1.96


def rankers(extra: dict) -> dict:
    """{name: rank callable or None (= S10 + L60)}. extra[(sym, day)] = (s10w, past60, leader)."""
    def get(sym, day):
        return extra.get((sym, day), (0.0, 0.0, False))
    return {
        "combined": None,
        "s10": lambda sym, day, sc: sc[0],
        "l60": lambda sym, day, sc: sc[1],
        "combined_waived": lambda sym, day, sc: get(sym, day)[0] + sc[1],
        "deepest_pullback": lambda sym, day, sc: -get(sym, day)[1],
        "leader_first": lambda sym, day, sc: (100.0 if get(sym, day)[2] else 0.0) + sc[0] + sc[1],
    }


def pick(train_metrics: dict) -> str:
    return max(train_metrics, key=lambda k: (train_metrics[k].get("sharpe") is not None,
                                            train_metrics[k].get("sharpe") or 0.0))


def verdict(picked: str, test: dict, dd_pick, dd_current) -> str:
    if picked == "combined":
        return "KEEP_CURRENT"
    m, t = test.get("mean_daily_pct"), test.get("t_hac")
    if m is not None and t is not None and m > 0 and t >= GATE_T and dd_pick >= dd_current - MAX_EXTRA_DD:
        return "RANKING_HELPS"
    return "INCONCLUSIVE"


def decide(out: dict, daily: dict) -> None:
    """Pick on train, test the pick's daily returns against `combined` on test, set the verdict."""
    picked = pick(out["train"])
    test = eg.diff_test(daily[("test", picked)], daily[("test", "combined")])
    out["picked_on_train"] = picked
    out["picked_minus_combined_test"] = test
    out["verdict"] = verdict(picked, test, out["test"][picked]["max_drawdown_pct"],
                             out["test"]["combined"]["max_drawdown_pct"])


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
    panel, extra = {}, {}
    for r in rows:
        panel.setdefault(r[F["sym"]], {})[r[F["day"]]] = [r[F["s10"]], r[F["l60"]]]
        extra[(r[F["sym"]], r[F["day"]])] = (r[F["s10w"]], r[F["past60"]], bool(r[F["leader"]]))
    symbols = sorted(set(panel) | {"SPY"})
    bars = {s: b for s in symbols if (ts := br.load_ohlcv(s)) and (b := pb.bars_from_series(ts))}
    first, last = min(a["day"]), max(a["day"])
    calendar = [d for d in sorted(bars["SPY"]["idx"]) if first <= d <= last]
    windows = {"train": [d for d in calendar if d < SPLIT], "test": [d for d in calendar if d >= SPLIT],
               "full": calendar}
    excluded = frozenset(s for s in symbols if instruments.is_excluded(s))
    gate = eg.in_set(eg.admit_set(a, wf.CURRENT))

    out = {"meta": {"run_date": str(datetime.date.today()), "split": SPLIT, "calendar": [calendar[0], calendar[-1]],
                    "symbols": len(symbols), "cost_bps": args.cost_bps}}
    daily = {}
    for window, cal in windows.items():
        out[window] = {}
        for name, rk in rankers(extra).items():
            res = pb.simulate(panel, bars, cal, risk_rules=True, cost_bps=args.cost_bps, excluded=excluded,
                              max_trade_risk_pct=CFG.system_max_trade_risk_pct, admit=gate, rank=rk)
            out[window][name] = pb.metrics(res)
            daily[(window, name)] = eg.daily_returns(res["curve"])
    decide(out, daily)
    sg._out(json.dumps({k: out[k] for k in ("meta", "picked_on_train", "picked_minus_combined_test", "verdict")},
                       indent=1))
    cols = ("cagr_pct", "total_return_pct", "max_drawdown_pct", "sharpe", "n_trades", "win_rate")
    for window in windows:
        sg._out(f"\n[{window}]  " + "  ".join(cols))
        for name, m in out[window].items():
            sg._out(f"  {name:<17}" + "".join(f"{str(m.get(c)):>12}" for c in cols))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)


if __name__ == "__main__":
    main()
