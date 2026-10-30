"""
Price-factor portfolio study (R&D #64): a broad, monthly-rebalanced "golden mix".

On the leak-free replay, aether_rules (5-6 names, swing exits on S10/L60) loses to SPY in all
50 robustness draws, and the Chaikin scores barely rank winners. This study tests the
best-documented price factors instead, computed from split-adjusted closes and known at the
rebalance day's close:

  MOM     return from MOM_LONG to MOM_SKIP trading days back (12-1 momentum)
  HIGH    close / highest close of the last HIGH_WIN days (nearness to the 52-week high)
  LOWVOL  minus the standard deviation of the last VOL_WIN daily log returns
  MIX     the average of the three cross-sectional percentile ranks

On each month's last trading day the eligible symbols (MIN_HISTORY days of history, not
instruments.is_excluded) are ranked and the top TOP_N are held equal-weight, traded at the
next day's open, 10 bps per side on the dollars traded. fac_mix_trend holds cash while SPY's
close is below its TREND_SMA-day average on the rebalance day.

Judged on R&D #62's draws (portfolio_backtest.draw_paths, seed 62): the variant with the best
median train Sharpe is the pick, and it passes only if it beats spy_buy_hold's Sharpe in at
least PASS_RATE of the draws on both halves. aether_rules and the equal-weight hold are
reported alongside (aether_rules needs the score panel, Data/portfolio_backtest_panel.json).
Point-in-time universe (--pit, the default; a correction found after the first run): a symbol
is rankable only from the date it joined our universe (its first scored day in the score panel),
and the equal-weight hold only buys symbols that had joined by its first day. Without it, ~90
symbols added in 2026 (several after big runs) were rankable from 2023 - look-ahead in the
choice of stocks that AETHER's own backtest doesn't have (it needs scores). --no-pit reproduces
the first, biased run. The universe is still today's list: survivorship bias remains.

Usage:
    python scripts/backtesting/factor_portfolio.py [--split 2025-01-01] [--robust 50]
"""
import argparse
import glob
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

import backtest_ratings as _br
import instruments
import portfolio_backtest as pb
from aether.config import CFG

TOP_N = 20
MOM_LONG = 252
MOM_SKIP = 21
HIGH_WIN = 252
VOL_WIN = 63
MIN_HISTORY = 253
TREND_SMA = 200
PASS_RATE = 0.8
FACTORS = ("mom", "high", "lowvol")
VARIANTS = {"fac_mom": ("mom", False), "fac_high": ("high", False), "fac_lowvol": ("lowvol", False),
            "fac_mix": ("mix", False), "fac_mix_trend": ("mix", True)}
OUT_PATH = os.path.join(BASE_DIR, "Data", "factor_portfolio.json")


def factor_values(b: dict, i: int) -> dict | None:
    """MOM / HIGH / LOWVOL for bar index i, from closes up to and including i (None if too short)."""
    if i + 1 < MIN_HISTORY:
        return None
    c = b["c"]
    if c[i - MOM_LONG] <= 0 or c[i] <= 0:
        return None
    window = np.asarray(c[i - VOL_WIN:i + 1], dtype=float)
    if np.any(window <= 0):
        return None
    return {"mom": c[i - MOM_SKIP] / c[i - MOM_LONG] - 1,
            "high": c[i] / max(c[i - HIGH_WIN + 1:i + 1]),
            "lowvol": -float(np.std(np.diff(np.log(window)), ddof=1))}


def pct_ranks(values: dict) -> dict:
    """Cross-sectional percentile rank in (0, 1]; ties share their average rank."""
    if not values:
        return {}
    order = sorted(values.items(), key=lambda kv: kv[1])
    n, out, k = len(order), {}, 0
    while k < n:
        j = k
        while j + 1 < n and order[j + 1][1] == order[k][1]:
            j += 1
        for m in range(k, j + 1):
            out[order[m][0]] = ((k + j) / 2 + 1) / n
        k = j + 1
    return out


def rank_symbols(bars: dict, universe, day: str, factor: str, join=None) -> list:
    """[(score, sym)] best first, for the symbols in `universe` with a bar and enough history.
    join: {sym: first day it was in our universe}; a symbol is skipped before that day."""
    vals = {}
    for sym in universe:
        if join is not None and day < join.get(sym, "9999-12-31"):
            continue
        b, i = pb._bar(bars, sym, day)
        if b is not None and (f := factor_values(b, i)) is not None:
            vals[sym] = f
    if factor == "mix":
        ranks = [pct_ranks({s: v[f] for s, v in vals.items()}) for f in FACTORS]
        scores = {s: sum(r[s] for r in ranks) / len(ranks) for s in vals}
    else:
        scores = {s: v[factor] for s, v in vals.items()}
    return sorted(((sc, s) for s, sc in scores.items()), reverse=True)


def month_ends(calendar: list) -> set:
    """Days whose next calendar day is in a different month (the final day never rebalances)."""
    return {d for d, nxt in zip(calendar, calendar[1:]) if d[:7] != nxt[:7]}


def spy_above_trend(spy: dict, day: str) -> bool:
    """SPY's close above its TREND_SMA-day average on `day` (True when there's too little history)."""
    i = spy["idx"].get(day)
    if i is None or i + 1 < TREND_SMA:
        return True
    return spy["c"][i] > sum(spy["c"][i - TREND_SMA + 1:i + 1]) / TREND_SMA


def simulate_factor(bars, calendar, universe, factor, *, trend=False, top_n=TOP_N, cost_bps=10.0,
                    initial=pb.INITIAL_EQUITY, join=None) -> dict:
    """Monthly top-N equal-weight portfolio; returns portfolio_backtest.metrics()-compatible output."""
    c = cost_bps / 10_000.0
    cash, qty, last = initial, {}, {}
    traded_usd, curve, target = 0.0, [], None
    rebal = month_ends(calendar)
    for t, day in enumerate(calendar):
        if target is not None:
            opens = {}
            for s in set(qty) | set(target):
                b, i = pb._bar(bars, s, day)
                if b is not None and b["o"][i] > 0:
                    opens[s] = b["o"][i]
            equity = cash + sum(q * opens.get(s, last.get(s, 0.0)) for s, q in qty.items())
            per = equity / len(target) if target else 0.0
            for s in list(qty):                                  # sells first
                if s not in opens:
                    continue
                excess = qty[s] * opens[s] - (per if s in target else 0.0)
                if excess > 1e-9:
                    cash += excess * (1 - c)
                    traded_usd += excess
                    qty[s] -= excess / opens[s]
                    if qty[s] * opens[s] < 1e-6:
                        del qty[s]
            for s in target:                                     # then buys
                if s not in opens:
                    continue
                need = per - qty.get(s, 0.0) * opens[s]
                amt = min(need, cash / (1 + c))
                if amt > 1e-9:
                    qty[s] = qty.get(s, 0.0) + amt / opens[s]
                    cash -= amt * (1 + c)
                    traded_usd += amt
            target = None
        for s in qty:
            b, i = pb._bar(bars, s, day)
            if b is not None:
                last[s] = b["c"][i]
        invested = sum(q * last.get(s, 0.0) for s, q in qty.items())
        curve.append((day, cash + invested, invested))
        if day in rebal:
            if trend and not spy_above_trend(bars["SPY"], day):
                target = []
            else:
                target = [s for _sc, s in rank_symbols(bars, universe, day, factor, join)[:top_n]]
    return {"curve": curve, "trades": [], "traded_usd": traded_usd, "open_positions": sorted(qty)}


def verdict(summary_vs_spy: dict) -> dict:
    """R&D #64 gate: pick the variant with the best median train Sharpe; pass only if it beats
    spy_buy_hold's Sharpe in at least PASS_RATE of the draws on both halves."""
    train, test = summary_vs_spy["periods"]["train"], summary_vs_spy["periods"]["test"]
    picked = max(VARIANTS, key=lambda v: train[v]["sharpe"]["median"]
                 if train[v]["sharpe"]["median"] is not None else -np.inf)
    w = (train[picked]["win_rate"], test[picked]["win_rate"])
    return {"picked": picked, "win_vs_spy_train": w[0], "win_vs_spy_test": w[1],
            "passed": bool(None not in w and min(w) >= PASS_RATE)}


def join_dates(panel: dict) -> dict:
    """{sym: first scored day}: the day a symbol entered our universe (point-in-time eligibility)."""
    return {s: min(by_day) for s, by_day in panel.items() if by_day}


def run(split="2025-01-01", robust=50, min_year=2023, cost_bps=10.0, pit=True) -> dict:
    ohlcv = {os.path.basename(f)[:-len("_daily.json")] for f in glob.glob(os.path.join(_br.OHLCV_DIR, "*_daily.json"))}
    cached = {d for d in os.listdir(_br.SYM_DIR) if os.path.isdir(os.path.join(_br.SYM_DIR, d))}
    symbols = sorted(ohlcv & cached)
    bars = {s: b for s in symbols if (ts := _br.load_ohlcv(s)) and (b := pb.bars_from_series(ts))}
    panel = None
    if os.path.exists(pb.PANEL_PATH):
        with open(pb.PANEL_PATH, encoding="utf-8") as f:
            panel = json.load(f)["scores"]
    start = f"{min_year}-01-01"
    if panel:
        start = min(min(v) for v in panel.values() if v)
    calendar = [d for d in sorted(bars["SPY"]["idx"]) if d >= start]
    excluded = frozenset(s for s in symbols if instruments.is_excluded(s))
    eligible = [s for s in symbols if s not in excluded and s in bars]
    if pit and not panel:
        raise SystemExit("--pit needs the score panel (Data/portfolio_backtest_panel.json) for join dates")
    join = join_dates(panel) if pit else None
    pb._out(f"\nFactor portfolio (R&D #64): {'point-in-time' if pit else 'today\'s'} universe, {len(symbols)} symbols, {calendar[0]} -> {calendar[-1]}, "
            f"top {TOP_N}, split {split}, {robust} draws")

    def run_table(syms, days):
        uni = [s for s in eligible if s in syms]
        ew_uni = [s for s in uni if join is None or join.get(s, "9999-12-31") <= days[0]]
        out = {v: pb.metrics(simulate_factor(bars, days, uni, f, trend=tr, cost_bps=cost_bps, join=join))
               for v, (f, tr) in VARIANTS.items()}
        out["spy_buy_hold"] = pb.metrics(pb.buy_and_hold(bars, days, ["SPY"], cost_bps))
        out["ew_buy_hold"] = pb.metrics(pb.buy_and_hold(bars, days, ew_uni, cost_bps))
        if panel:
            sub = {s: panel[s] for s in syms if s in panel}
            out["aether_rules"] = pb.metrics(pb.simulate(sub, bars, days, cost_bps=cost_bps, excluded=excluded,
                                                         max_trade_risk_pct=CFG.system_max_trade_risk_pct))
        return out

    paths = pb.draw_paths(symbols, calendar, robust, split=split)
    per_draw = []
    for path in paths:
        full = [d for d in calendar if d >= path["start"]]
        per_draw.append({"full": run_table(path["symbols"], full),
                         "train": run_table(path["symbols"], [d for d in full if d < split]),
                         "test": run_table(path["symbols"], [d for d in full if d >= split])})
    vs_spy = pb.robustness_summary(per_draw, base="spy_buy_hold")
    vs_aether = pb.robustness_summary(per_draw, base="aether_rules") if panel else None
    v = verdict(vs_spy)

    for period, rows in vs_spy["periods"].items():
        pb._out(f"\n  {period}: Sharpe median [p10, p90]   CAGR% med   MaxDD% med   win vs SPY"
                + ("   win vs aether" if vs_aether else ""))
        for name, r in rows.items():
            sh = r["sharpe"]
            wa = vs_aether["periods"][period][name]["win_rate"] if vs_aether and name != "aether_rules" else None
            pb._out(f"  {name:<15}{sh['median']!s:>7} [{sh['p10']!s:>6},{sh['p90']!s:>6}]"
                    f"{r['cagr_pct']['median']!s:>11}{r['max_drawdown_pct']['median']!s:>13}"
                    f"{'-' if name == 'spy_buy_hold' else r['win_rate']!s:>12}"
                    + (f"{'-' if wa is None else wa!s:>16}" if vs_aether else ""))
    pb._out(f"\n  R&D #64: picked on train {v['picked']} -> beats SPY in {v['win_vs_spy_train']:.0%} (train) / "
            f"{v['win_vs_spy_test']:.0%} (test) of draws -> "
            f"{'PASS' if v['passed'] else 'FAIL: not a golden mix'} (bar {PASS_RATE:.0%} on both)")
    payload = {"as_of": calendar[-1], "n_symbols": len(symbols), "split": split, "n_draws": robust, "pit": pit,
               "top_n": TOP_N, "verdict": v, "vs_spy": vs_spy, "vs_aether": vs_aether,
               "caveats": ["survivorship bias: universe is today's symbol list (flatters MOM and HIGH)",
                           "draws overlap heavily; win rates are sensitivity, not probability (R&D #62)"]}
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1)
    pb._out(f"\n  Wrote {OUT_PATH}")
    return payload


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--split", default="2025-01-01", type=pb._iso_date)
    ap.add_argument("--robust", type=int, default=50)
    ap.add_argument("--min-year", type=int, default=2023)
    ap.add_argument("--cost-bps", type=float, default=10.0)
    ap.add_argument("--no-pit", dest="pit", action="store_false",
                    help="rank every symbol from 2023 (the first, look-ahead-biased run)")
    a = ap.parse_args()
    run(a.split, a.robust, a.min_year, a.cost_bps, a.pit)
