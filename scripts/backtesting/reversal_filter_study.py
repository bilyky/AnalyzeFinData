"""
Short-term reversal in the portfolio (R&D #86, follow-up to #85): does skipping stocks that just
ran up help today's rules, and does a plain "buy the 3-day losers" sleeve beat SPY?

R&D #85 (feature IC by subgroup) found one confirmed input: the 3-session return orders the next
20 sessions' returns with a NEGATIVE sign (test IC -0.081, t -4.32): stocks up most over 3 days lag
over the next 20 sessions. An IC is not a portfolio: this asks whether it survives the rules,
sizing, stops and costs.

The 3-session return (ret3) on day d = close_d / close_{d-3} - 1, split-adjusted
(portfolio_backtest.bars_from_series), known at d's close. Ranked across the symbols scored on d
(the score panel's point-in-time universe, instruments.is_excluded names left out).

Variants (portfolio_backtest.simulate unchanged: regime profile, thresholds, cash buffers, ATR
stops, sell_rules exits, the 1% per-trade loss cap, slot expansion, 10 bps a side):
  aether_rules     today's rules
  aether_no_runup  the same, but a candidate in the top RUNUP_FRAC (third) of ret3 that day is
                   skipped (simulate's admit hook); days with < MIN_NAMES scored names: no filter
  reversal_q       the bottom REV_FRAC (fifth) of ret3 among the day's scored names, equal weight,
                   rebalanced on each month's last trading day (factor_portfolio.simulate_factor),
                   no stops: a pure test of the signal at its measured 20-session horizon
  spy_buy_hold, rsp_buy_hold   the benchmarks

Robustness: R&D #62's 50 draws (seed 62: 80% of the symbols, a start 0-60 days late), split
2025-01-01; each draw runs full / train / test from fresh cash.

Decision rules (fixed before the first run):
  1. FILTER  aether_no_runup's win rate against aether_rules (share of draws with a higher
             Sharpe on the same path) on both halves: >= 0.8 -> FILTER_HELPS (propose it as an
             entry gate, its own PR, gated on the risk rules); <= 0.2 -> FILTER_HURTS; else
             INCONCLUSIVE (no change).
  2. SLEEVE  reversal_q's win rate against spy_buy_hold on both halves >= 0.8 -> BEATS_SPY
             (propose a small sleeve study in the paper game); else NO_EDGE_VS_SPY.
NOT independent evidence: #85's test years (2025-26) are this study's test half, and the
hypothesis came from them. A pass here says the IC survives the rules and costs on the same data;
the out-of-sample check is a re-run on 2027 data. Draws overlap heavily, so win rates are
sensitivity, not probability (R&D #62).

Not measured: overrides, scale-outs, rotation and the other live extras portfolio_backtest
doesn't simulate; taxes; slippage beyond 10 bps (a monthly quintile rebalance turns most of the
sleeve over). Biases: today's universe (survivorship), snapshots after the R&D #74 quarantine.

Usage (from a worktree, reading the real caches):
    AETHER_CACHE_DIR=<main>/Data python scripts/backtesting/reversal_filter_study.py [--draws 50]
"""
import argparse
import contextlib
import datetime
import glob
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

import backtest_ratings as _br  # noqa: E402
import factor_portfolio as fp  # noqa: E402
import instruments  # noqa: E402
import portfolio_backtest as pb  # noqa: E402
from aether import paths  # noqa: E402
from aether.config import CFG  # noqa: E402

RUNUP_FRAC = 1 / 3
REV_FRAC = 0.20
MIN_NAMES = 10
LOOKBACK = 3
PASS = fp.PASS_RATE
WIN, LOSE = pb.ROBUST_WIN, pb.ROBUST_LOSE


def ret3(bars, sym, day):
    """close_day / close_{day-3} - 1 on split-adjusted bars, or None."""
    b, i = pb._bar(bars, sym, day)
    if b is None or i < LOOKBACK or b["c"][i - LOOKBACK] <= 0:
        return None
    return b["c"][i] / b["c"][i - LOOKBACK] - 1


def _scored_ret3(panel, bars, day, excluded):
    return [(r, s) for s, by_day in panel.items()
            if s not in excluded and by_day.get(day) is not None and (r := ret3(bars, s, day)) is not None]


def runup_sets(panel, bars, calendar, excluded, frac=RUNUP_FRAC) -> dict:
    """{day: symbols in the top `frac` of ret3 among that day's scored names} (>= MIN_NAMES only)."""
    out = {}
    for day in calendar:
        vals = sorted(_scored_ret3(panel, bars, day, excluded), reverse=True)
        if len(vals) >= MIN_NAMES:
            out[day] = {s for _r, s in vals[:math.ceil(len(vals) * frac)]}
    return out


def no_runup_admit(runups: dict):
    """simulate's admit hook: skip a candidate that is in the day's run-up set."""
    return lambda sym, day: sym not in runups.get(day, ())


def reversal_ranker(panel, excluded, frac=REV_FRAC):
    """A factor_portfolio.rank_symbols replacement: the bottom `frac` of ret3 among the symbols
    scored on `day` (point-in-time), biggest 3-day loser first."""
    def rank(bars, universe, day, factor, join=None):
        allowed = set(universe)
        vals = sorted(((-r, s) for r, s in _scored_ret3(panel, bars, day, excluded) if s in allowed), reverse=True)
        return vals[:max(1, math.ceil(len(vals) * frac))] if len(vals) >= MIN_NAMES else []
    return rank


@contextlib.contextmanager
def _ranker(fn):
    saved = fp.rank_symbols
    fp.rank_symbols = fn
    try:
        yield
    finally:
        fp.rank_symbols = saved


def run_table(panel, bars, eligible, excluded, cost_bps, runups):
    """run_table(symbols, days) -> {variant: metrics}, for portfolio_backtest.run_robustness."""
    cap = CFG.system_max_trade_risk_pct

    def table(syms, days):
        sub, elig = pb.path_universe(panel, eligible, syms)
        kw = {"risk_rules": True, "cost_bps": cost_bps, "excluded": excluded, "max_trade_risk_pct": cap}
        out = {"aether_rules": pb.metrics(pb.simulate(sub, bars, days, **kw)),
               "aether_no_runup": pb.metrics(pb.simulate(sub, bars, days, admit=no_runup_admit(runups), **kw))}
        with _ranker(reversal_ranker(sub, excluded)):
            out["reversal_q"] = pb.metrics(fp.simulate_factor(bars, days, elig, "ret3", top_n=10 ** 6,
                                                              cost_bps=cost_bps))
        out["spy_buy_hold"] = pb.metrics(pb.buy_and_hold(bars, days, ["SPY"], cost_bps))
        if "RSP" in bars:
            out["rsp_buy_hold"] = pb.metrics(pb.buy_and_hold(bars, days, ["RSP"], cost_bps))
        return out
    return table


def win_rate(per_draw_sharpe, a, b, period):
    """Share of draws where a's Sharpe beats b's in `period` (draws missing either are skipped)."""
    pairs = [(d[period].get(a), d[period].get(b)) for d in per_draw_sharpe]
    pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
    return round(sum(x > y for x, y in pairs) / len(pairs), 3) if pairs else None


def verdicts(summary) -> dict:
    pds = summary["per_draw_sharpe"]
    fw = (win_rate(pds, "aether_no_runup", "aether_rules", "train"),
          win_rate(pds, "aether_no_runup", "aether_rules", "test"))
    if None not in fw and min(fw) >= WIN:
        f = "FILTER_HELPS"
    elif None not in fw and max(fw) <= LOSE:
        f = "FILTER_HURTS"
    else:
        f = "INCONCLUSIVE"
    sw = (win_rate(pds, "reversal_q", "spy_buy_hold", "train"), win_rate(pds, "reversal_q", "spy_buy_hold", "test"))
    s = "BEATS_SPY" if None not in sw and min(sw) >= PASS else "NO_EDGE_VS_SPY"
    ctx = {f"{a}_vs_{b}": {p: win_rate(pds, a, b, p) for p in ("train", "test")}
           for a, b in (("aether_no_runup", "spy_buy_hold"), ("reversal_q", "rsp_buy_hold"),
                        ("reversal_q", "aether_rules"), ("aether_rules", "spy_buy_hold"))}
    return {"filter": {"win_vs_aether": {"train": fw[0], "test": fw[1]}, "verdict": f},
            "sleeve": {"win_vs_spy": {"train": sw[0], "test": sw[1]}, "verdict": s}, "context": ctx}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--draws", type=int, default=50)
    ap.add_argument("--split", default="2025-01-01")
    ap.add_argument("--min-year", type=int, default=2023)
    ap.add_argument("--cost-bps", type=float, default=10.0)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--max-symbols", type=int, default=None, help="smoke test on a slice (don't read results)")
    a = ap.parse_args(argv)
    ohlcv = {os.path.basename(f)[:-len("_daily.json")] for f in glob.glob(os.path.join(_br.OHLCV_DIR, "*_daily.json"))}
    cached = {d for d in os.listdir(_br.SYM_DIR) if os.path.isdir(os.path.join(_br.SYM_DIR, d))}
    symbols = sorted(ohlcv & cached)
    if a.max_symbols:
        symbols = sorted(set(symbols[:a.max_symbols]) | ({"SPY", "RSP"} & set(symbols)))
    panel = pb.build_panel(symbols, a.min_year, a.workers)
    bars = {s: b for s in set(symbols) | {"RSP"} if (ts := _br.load_ohlcv(s)) and (b := pb.bars_from_series(ts))}
    spy_days = sorted(bars["SPY"]["idx"])
    first_score = min(min(by_day) for by_day in panel.values() if by_day)
    calendar = [d for d in spy_days if d >= first_score]
    excluded = frozenset(s for s in symbols if instruments.is_excluded(s))
    eligible = [s for s in symbols if s not in excluded]
    split = datetime.date.fromisoformat(a.split).isoformat()
    runups = runup_sets(panel, bars, calendar, excluded)
    summary = pb.run_robustness(pb.draw_paths(symbols, calendar, a.draws, split=split), calendar, split,
                                run_table(panel, bars, eligible, excluded, a.cost_bps, runups))
    v = verdicts(summary)
    rep = {"run": datetime.datetime.now().isoformat(timespec="seconds"), "as_of": calendar[-1],
           "first_day": calendar[0], "n_symbols": len(symbols), "split": split, "draws": a.draws,
           "cost_bps": a.cost_bps, "runup_frac": RUNUP_FRAC, "rev_frac": REV_FRAC, "filtered_days": len(runups),
           "periods": summary["periods"], "verdicts": v, "per_draw_sharpe": summary["per_draw_sharpe"],
           "smoke_test": bool(a.max_symbols)}
    out = os.path.join(paths.data_dir(), f"reversal_filter_study_{datetime.datetime.now():%Y-%m-%d_%H%M%S}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1)
    for period in ("train", "test"):
        sys.stdout.write(f"\n{period}: median Sharpe / CAGR% / MaxDD%\n")
        for name, r in summary["periods"][period].items():
            sys.stdout.write(f"  {name:<16} {r['sharpe']['median']!s:>6} {r['cagr_pct']['median']!s:>7} "
                             f"{r['max_drawdown_pct']['median']!s:>7}\n")
    sys.stdout.write("\nverdicts: " + json.dumps(v) + f"\nwrote {out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
