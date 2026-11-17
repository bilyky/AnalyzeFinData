"""
Breadth study (R&D #83): does holding more names, on the same scores and rules, close the gap
to SPY?

The live game holds 5-6 names (3 in DEFENSIVE), set when the presets were written (2026-06-22)
with no study behind the number. On the leak-free replay those portfolios lose to SPY in all 50
robustness draws (R&D #62/#64), and the scores' rank IC is ~0.01 (R&D #60). A weak signal can
only show through many independent bets; 5-6 names leave it under each stock's own noise. This
asks whether breadth, not the scores, is the bottleneck.

Variants (portfolio_backtest.simulate unchanged: regime profile from SPY L60, thresholds, cash
buffers, ATR stops, sell_rules exits, the 1% per-trade loss cap, slot expansion, 10 bps a side):
  aether_rules     today's PROFILE_RULES (AGGRESSIVE 6, BALANCED 5, DEFENSIVE 3 names)
  breadth_N        N = 10, 20, 40 names in every profile; per-name cap min(today's cap, 1/N)
  score_tilt_q     the top fifth of the scored universe by S10 + L60, equal weight, rebalanced
                   on each month's last trading day (factor_portfolio.simulate_factor, ranked by
                   the app's scores instead of price factors); no stops: a pure test of the scores
  spy_buy_hold, rsp_buy_hold   the benchmarks

Robustness: R&D #62's 50 draws (portfolio_backtest.draw_paths, seed 62: 80% of the symbols, a
start 0-60 days late), split 2025-01-01; each draw runs full / train / test from fresh cash.

Decision rules (fixed before the first run):
  1. BREADTH  pick the breadth_N with the best median TRAIN Sharpe. Its win rate against
              aether_rules (share of draws where its Sharpe is higher on the same path) on
              both halves: >= 0.8 -> BREADTH_HELPS (propose more slots, its own PR, gated on the
              risk rules); <= 0.2 -> BREADTH_HURTS; else INCONCLUSIVE (keep today's slots).
  2. VS SPY   pick, among aether_rules, breadth_* and score_tilt_q, the best median TRAIN Sharpe.
              Its win rate against spy_buy_hold on both halves >= 0.8 (factor_portfolio's
              PASS_RATE) -> BEATS_SPY; else NO_EDGE_VS_SPY (the scores don't earn their keep
              against simply holding SPY, at any breadth tested).
Draws overlap heavily (80% of the same symbols), so win rates are sensitivity, not probability
(R&D #62). Two pre-registered questions; four breadth variants: one pass is weak evidence.

Not measured: overrides, scale-outs, rotation and the other live extras portfolio_backtest
doesn't simulate; taxes. Biases: today's universe (survivorship), snapshots after the R&D #74
quarantine; score_tilt_q ranks only symbols scored that day (point-in-time joins).

Usage (from a worktree, reading the real caches):
    AETHER_CACHE_DIR=<main>/Data python scripts/backtesting/breadth_study.py [--draws 50] [--split 2025-01-01]
"""
import argparse
import contextlib
import copy
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

BREADTHS = (10, 20, 40)
TILT_FRAC = 0.20
WIN, LOSE = pb.ROBUST_WIN, pb.ROBUST_LOSE
PASS = fp.PASS_RATE
STRATEGIES = ("aether_rules", *(f"breadth_{n}" for n in BREADTHS), "score_tilt_q")


@contextlib.contextmanager
def profile_breadth(n):
    """PROFILE_RULES with n names in every profile and the per-name cap at min(today's, 1/n)."""
    saved = pb.PROFILE_RULES
    rules = copy.deepcopy(saved)
    for r in rules.values():
        r["max_positions"] = n
        r["max_allocation_pct"] = min(r["max_allocation_pct"], 1.0 / n)
    pb.PROFILE_RULES = rules
    try:
        yield rules
    finally:
        pb.PROFILE_RULES = saved


def score_ranker(panel, frac=TILT_FRAC):
    """A factor_portfolio.rank_symbols replacement: the top `frac` of the symbols scored on `day`
    (point-in-time: only symbols with a score that day), best S10 + L60 first."""
    def rank(bars, universe, day, factor, join=None):
        scored = []
        for s in universe:
            v = panel.get(s, {}).get(day)
            if v is not None and pb._bar(bars, s, day)[0] is not None:
                scored.append((float(v[0]) + float(v[1]), s))
        scored.sort(reverse=True)
        return scored[:max(1, math.ceil(len(scored) * frac))] if scored else []
    return rank


def run_table(panel, bars, eligible, excluded, cost_bps):
    """run_table(symbols, days) -> {variant: metrics}, for portfolio_backtest.run_robustness."""
    cap = CFG.system_max_trade_risk_pct

    def table(syms, days):
        sub, elig = pb.path_universe(panel, eligible, syms)
        out = {"aether_rules": pb.metrics(pb.simulate(sub, bars, days, risk_rules=True, cost_bps=cost_bps,
                                                      excluded=excluded, max_trade_risk_pct=cap))}
        for n in BREADTHS:
            with profile_breadth(n):
                out[f"breadth_{n}"] = pb.metrics(pb.simulate(sub, bars, days, risk_rules=True, cost_bps=cost_bps,
                                                             excluded=excluded, max_trade_risk_pct=cap))
        saved = fp.rank_symbols
        fp.rank_symbols = score_ranker(sub)
        try:
            out["score_tilt_q"] = pb.metrics(fp.simulate_factor(bars, days, elig, "score", top_n=10 ** 6,
                                                                cost_bps=cost_bps))
        finally:
            fp.rank_symbols = saved
        out["spy_buy_hold"] = pb.metrics(pb.buy_and_hold(bars, days, ["SPY"], cost_bps))
        if "RSP" in bars:
            out["rsp_buy_hold"] = pb.metrics(pb.buy_and_hold(bars, days, ["RSP"], cost_bps))
        return out
    return table


def win_rates(per_draw_sharpe, a, b, period):
    """Share of draws where a's Sharpe beats b's in `period` (draws missing either are skipped)."""
    pairs = [(d[period].get(a), d[period].get(b)) for d in per_draw_sharpe]
    pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
    return round(sum(x > y for x, y in pairs) / len(pairs), 3) if pairs else None


def _median_train_sharpe(summary, v):
    s = summary["periods"]["train"].get(v, {}).get("sharpe", {})
    return s.get("median") if s.get("median") is not None else -math.inf


def verdicts(summary) -> dict:
    """The two pre-registered verdicts from a run_robustness summary (with per_draw_sharpe)."""
    pds = summary["per_draw_sharpe"]
    b_pick = max((f"breadth_{n}" for n in BREADTHS), key=lambda v: _median_train_sharpe(summary, v))
    bw = (win_rates(pds, b_pick, "aether_rules", "train"), win_rates(pds, b_pick, "aether_rules", "test"))
    if None not in bw and min(bw) >= WIN:
        b = "BREADTH_HELPS"
    elif None not in bw and max(bw) <= LOSE:
        b = "BREADTH_HURTS"
    else:
        b = "INCONCLUSIVE"
    s_pick = max(STRATEGIES, key=lambda v: _median_train_sharpe(summary, v))
    sw = (win_rates(pds, s_pick, "spy_buy_hold", "train"), win_rates(pds, s_pick, "spy_buy_hold", "test"))
    s = "BEATS_SPY" if None not in sw and min(sw) >= PASS else "NO_EDGE_VS_SPY"
    return {"breadth": {"picked": b_pick, "win_vs_aether": {"train": bw[0], "test": bw[1]}, "verdict": b},
            "vs_spy": {"picked": s_pick, "win_vs_spy": {"train": sw[0], "test": sw[1]}, "verdict": s},
            "win_vs_spy_all": {v: {p: win_rates(pds, v, "spy_buy_hold", p) for p in ("train", "test")}
                               for v in STRATEGIES}}


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
    summary = pb.run_robustness(pb.draw_paths(symbols, calendar, a.draws, split=split), calendar, split,
                                run_table(panel, bars, eligible, excluded, a.cost_bps))
    v = verdicts(summary)
    rep = {"run": datetime.datetime.now().isoformat(timespec="seconds"), "as_of": calendar[-1],
           "first_day": calendar[0], "n_symbols": len(symbols), "split": split, "draws": a.draws,
           "cost_bps": a.cost_bps, "breadths": list(BREADTHS), "tilt_frac": TILT_FRAC,
           "periods": summary["periods"], "verdicts": v, "per_draw_sharpe": summary["per_draw_sharpe"],
           "smoke_test": bool(a.max_symbols)}
    out = os.path.join(paths.data_dir(), f"breadth_study_{datetime.datetime.now():%Y-%m-%d_%H%M%S}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1)
    for period in ("train", "test"):
        sys.stdout.write(f"\n{period}: median Sharpe / CAGR% / MaxDD%\n")
        for name, r in summary["periods"][period].items():
            sys.stdout.write(f"  {name:<14} {r['sharpe']['median']!s:>6} {r['cagr_pct']['median']!s:>7} "
                             f"{r['max_drawdown_pct']['median']!s:>7}\n")
    sys.stdout.write("\nverdicts: " + json.dumps({k: v[k] for k in ("breadth", "vs_spy")}) + f"\nwrote {out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
