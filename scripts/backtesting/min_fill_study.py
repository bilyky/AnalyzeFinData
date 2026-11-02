"""R&D #65: should a buy that the 1% loss cap trims to a sliver be skipped?

PROD 2026-11-02 holds NCLH at $52 (3 shares) and MEDP at 1 share on $10.5K equity. Live has no
minimum position size: the loss cap trims a buy to whatever keeps (entry - stop) * qty under 1% of
equity, and anything of 1 share or more fills. A sliver can barely move the portfolio but still
costs a slot, commissions and attention.

Variants (portfolio_backtest.simulate, today's rules, 1% loss cap, 10 bps/side): no floor (live),
and skipping a trimmed buy worth less than 1%, 2% or 4% of equity (min_fill_pct).

Decision rule (committed together with the first result):
- Train 2023-01..2024-12, test 2025-01..end. Pick the floor with the best TRAIN Sharpe.
- MIN_FILL_HELPS only if, on the TEST window, the pick's daily return minus no-floor's has HAC
  t >= 1.96 (lag 5, as R&D #58), mean > 0, and its max drawdown is no more than 2 points deeper.
- MIN_FILL_HURTS if that difference has t <= -1.96. Otherwise INCONCLUSIVE: keep live as is.

Not measured: whole-share rounding (the simulator uses fractional shares, so slivers here are
smaller than live's 1-share minimum), slot pressure from held slivers beyond what the slot cap
does, scale-outs and pyramid adds (not simulated). Universe = today's symbols (survivorship).

    python scripts/backtesting/min_fill_study.py [--split 2025-01-01] [--out FILE]
"""

import argparse
import datetime
import glob
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))

import backtest_ratings as _br
import entry_gates_portfolio as eg
import portfolio_backtest as pb
import instruments
from aether.config import CFG

FLOORS = {"no_floor": None, "floor_1pct": 0.01, "floor_2pct": 0.02, "floor_4pct": 0.04}
GATE_T = 1.96
MAX_EXTRA_DD = 2.0


def run_floors(panel, bars, calendar, excluded, cost_bps=10.0) -> dict:
    cap = CFG.system_max_trade_risk_pct
    return {name: pb.simulate(panel, bars, calendar, risk_rules=True, cost_bps=cost_bps,
                              excluded=excluded, max_trade_risk_pct=cap, min_fill_pct=f)
            for name, f in FLOORS.items()}


def pick(train_metrics: dict) -> str:
    """Best train Sharpe among the floors (no_floor excluded: it is the baseline)."""
    cands = [k for k in FLOORS if k != "no_floor" and train_metrics[k].get("sharpe") is not None]
    return max(cands, key=lambda k: train_metrics[k]["sharpe"]) if cands else "no_floor"


def verdict(diff: dict, dd_pick, dd_base) -> str:
    m, t = diff.get("mean_daily_pct"), diff.get("t_hac")
    if m is None or t is None:
        return "INCONCLUSIVE"
    if m > 0 and t >= GATE_T and dd_pick >= dd_base - MAX_EXTRA_DD:
        return "MIN_FILL_HELPS"
    if m < 0 and t <= -GATE_T:
        return "MIN_FILL_HURTS"
    return "INCONCLUSIVE"


def run(split="2025-01-01", min_year=2023, out=None) -> dict:
    ohlcv = {os.path.basename(f)[:-len("_daily.json")] for f in glob.glob(os.path.join(_br.OHLCV_DIR, "*_daily.json"))}
    cached = {d for d in os.listdir(_br.SYM_DIR) if os.path.isdir(os.path.join(_br.SYM_DIR, d))}
    symbols = sorted(ohlcv & cached)
    panel = pb.build_panel(symbols, min_year, max(1, (os.cpu_count() or 2) - 1))
    bars = {s: b for s in symbols if (ts := _br.load_ohlcv(s)) and (b := pb.bars_from_series(ts))}
    first = min(min(d) for d in panel.values() if d)
    calendar = [d for d in sorted(bars["SPY"]["idx"]) if d >= first]
    excluded = frozenset(s for s in symbols if instruments.is_excluded(s))
    train_cal = [d for d in calendar if d < split]
    test_cal = [d for d in calendar if d >= split]

    full = run_floors(panel, bars, calendar, excluded)
    train = {k: pb.metrics(v) for k, v in run_floors(panel, bars, train_cal, excluded).items()}
    test_runs = run_floors(panel, bars, test_cal, excluded)
    test = {k: pb.metrics(v) for k, v in test_runs.items()}
    picked = pick(train)
    diff = eg.diff_test(eg.daily_returns(test_runs[picked]["curve"]),
                        eg.daily_returns(test_runs["no_floor"]["curve"]))
    rep = {
        "meta": {"run_date": str(datetime.date.today()), "split": split, "symbols": len(symbols),
                 "calendar": [calendar[0], calendar[-1]], "floors": FLOORS},
        "full": {k: pb.metrics(v) for k, v in full.items()},
        "train": train, "test": test, "picked": picked, "test_diff_vs_no_floor": diff,
        "trades": {k: len(v["trades"]) for k, v in full.items()},
        "verdict": verdict(diff, test[picked].get("max_drawdown_pct", -1e9),
                           test["no_floor"].get("max_drawdown_pct", -1e9)),
    }
    out = out or os.path.join(pb.DATA_DIR, f"min_fill_study_{rep['meta']['run_date']}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=2)
    pb._out(json.dumps({k: rep[k] for k in ("picked", "test_diff_vs_no_floor", "verdict", "trades")}, indent=1))
    for w in ("train", "test", "full"):
        pb._out(f"  {w}: " + "; ".join(f"{k} CAGR {m.get('cagr_pct')} DD {m.get('max_drawdown_pct')} "
                                       f"Sharpe {m.get('sharpe')}" for k, m in rep[w].items()))
    pb._out(f"report -> {out}")
    return rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="2025-01-01")
    ap.add_argument("--min-year", type=int, default=2023)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    run(a.split, a.min_year, a.out)


if __name__ == "__main__":
    main()
