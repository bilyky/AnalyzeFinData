"""
Pre-report risk: is a buy made shortly before an earnings report more likely to suffer a big drop?
(R&D #98 step 2, owner order R2, 2026-11-24.) Pre-registered 2026-11-24.

Why: 9 of the 10 missed winners of 2026-10-22 made their biggest move on the first session after a
report, and SA fell ~30% on 2026-11-19 (a foreign filer, not in this data). The owner's bar is risk
first: a rule that cuts big losses is worth having even if it does not add return, as long as it does
not give up a clear gain.

DATA
  Report dates: Data/earnings_history.json (aether.earnings_history: SEC 8-K item 2.02, the first
  session after each release). Prices: split-adjusted daily closes (portfolio_backtest.bars_from_series)
  for every symbol with reports, and SPY. Days 2005-01-03 .. the last bar with a full horizon.

GROUPS (each stock on each day t, decided at t's close; H = 10 sessions, the Short10 horizon)
  before   the stock's next report's first session falls in t+1 .. t+H (a 10-day hold would carry
           the report)
  clean    no report first session in t .. t+2H (no report today, none in the next 20 sessions)
  Days in neither group (a report 11-20 sessions ahead, or today) are left out.

OUTCOMES (from the close of t)
  big_drop  the worst close in t+1 .. t+H is 15% or more below the close of t (R&D #113's measure)
  excess    close(t+H) / close(t) minus SPY's same ratio

TEST
  Per date with at least MIN_PER_GROUP (5) stocks in each group: mean(before) - mean(clean) of each
  outcome. Newey-West t over those daily differences, lag H - 1 (overlapping windows). Both halves
  (before / from SPLIT = 2016-01-01) must agree.

DECISION RULE
  RULE_HELPS      big_drop difference > 0 with HAC t >= 2 in both halves, AND the excess difference
                  does not have HAC t >= 2 in either half (skipping these buys gives up no clear gain)
                  -> propose "no new buy within 10 sessions before a known report" for the rule
                  shadow log (T0.4) and a portfolio test; nothing is wired from this study.
  TRADE_OFF       big_drop difference t >= 2 in both halves AND excess difference t >= 2 in both
                  -> report both; the owner decides.
  RULE_COSTS      excess difference t >= 2 in both halves and big_drop not t >= 2 in both -> keep buying.
  INCONCLUSIVE    anything else -> nothing changes.

REPORTED, NOT JUDGED
  H = 20 (Long60 is longer, but 20 is the standard check); the share of a stock's absolute daily
  moves that falls on report first sessions (how much of the action is earnings); counts per group.

NOT MEASURED / BIASES
  Assumes the report date is known H sessions ahead: companies usually announce it 2-4 weeks before,
  and the live earnings calendar (aether.earnings_calendar) has it for many stocks but not all.
  Today's symbol list; foreign filers (no 8-K) are absent; stops, sizing and the portfolio are not
  modelled (a portfolio test is the next step if this passes).

Usage (reading the real caches from a worktree):
    AETHER_DATA_DIR=<main>/Data AETHER_CACHE_DIR=<main>/Data python scripts/backtesting/earnings_risk_study.py
"""
import argparse
import datetime
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

import backtest_ratings as _br
import portfolio_backtest as pb
from aether import earnings_history, paths
from setup_s10_gate_study import _t_hac

H = 10
H_CONTEXT = 20
START = "2005-01-03"
SPLIT = "2016-01-01"
BIG_DROP = -0.15
MIN_PER_GROUP = 5
GATE_T = 2.0


def report_index(days: list, sessions: set) -> np.ndarray:
    """For each bar index i, sessions until the next report first session at or after i (inf if none)."""
    n = len(days)
    out = np.full(n, np.inf)
    nxt = np.inf
    for i in range(n - 1, -1, -1):
        if days[i] in sessions:
            nxt = i
        out[i] = nxt - i
    return out


def symbol_rows(days: list, closes: list, spy: dict, sessions: set, h: int = H, start: str = START) -> list:
    """(day, group, big_drop, excess) for one stock; group 'before' or 'clean' (see the docstring)."""
    c = np.asarray(closes, dtype=float)
    ahead = report_index(days, sessions)
    rows = []
    for i in range(len(days) - h):
        d = days[i]
        if d < start or c[i] <= 0 or d not in spy or days[i + h] not in spy:
            continue
        a = ahead[i]
        if 1 <= a <= h:
            group = "before"
        elif a > 2 * h:
            group = "clean"
        else:
            continue
        worst = c[i + 1:i + h + 1].min() / c[i] - 1
        excess = (c[i + h] / c[i]) - (spy[days[i + h]] / spy[d])
        rows.append((d, group, int(worst <= BIG_DROP), float(excess)))
    return rows


def daily_differences(rows: list, min_per_group: int = MIN_PER_GROUP) -> dict:
    """{day: (big_drop diff, excess diff, n_before, n_clean)} for days with enough stocks in both groups."""
    acc = {}
    for d, g, bd, ex in rows:
        a = acc.setdefault(d, {"before": [[], []], "clean": [[], []]})
        a[g][0].append(bd)
        a[g][1].append(ex)
    out = {}
    for d, a in acc.items():
        nb, nc = len(a["before"][0]), len(a["clean"][0])
        if nb >= min_per_group and nc >= min_per_group:
            out[d] = (np.mean(a["before"][0]) - np.mean(a["clean"][0]),
                      np.mean(a["before"][1]) - np.mean(a["clean"][1]), nb, nc)
    return out


def half_stats(diffs: dict, lo: str, hi: str | None, h: int = H) -> dict:
    days = sorted(d for d in diffs if d >= lo and (hi is None or d < hi))
    bd = [diffs[d][0] for d in days]
    ex = [diffs[d][1] for d in days]
    return {"days": len(days),
            "big_drop_diff_pct": round(100 * float(np.mean(bd)), 3) if bd else None,
            "big_drop_t": (round(t, 2) if (t := _t_hac(bd, h - 1)) is not None else None),
            "excess_diff_pct": round(100 * float(np.mean(ex)), 3) if ex else None,
            "excess_t": (round(t, 2) if (t := _t_hac(ex, h - 1)) is not None else None),
            "mean_n_before": round(float(np.mean([diffs[d][2] for d in days])), 1) if days else None,
            "mean_n_clean": round(float(np.mean([diffs[d][3] for d in days])), 1) if days else None}


def verdict(halves: list) -> str:
    def sig_pos(h, key):
        t = h[f"{key}_t"]
        return t is not None and t >= GATE_T
    drop_both = all(sig_pos(h, "big_drop") for h in halves)
    gain_both = all(sig_pos(h, "excess") for h in halves)
    gain_any = any(sig_pos(h, "excess") for h in halves)
    if drop_both and gain_both:
        return "TRADE_OFF"
    if drop_both and not gain_any:
        return "RULE_HELPS"
    if gain_both:
        return "RULE_COSTS"
    return "INCONCLUSIVE"


def report_day_share(days: list, closes: list, sessions: set) -> float | None:
    """Share of the stock's summed |daily return| that falls on report first sessions."""
    c = np.asarray(closes, dtype=float)
    if len(c) < 2:
        return None
    r = np.abs(np.diff(c) / c[:-1])
    on = np.array([d in sessions for d in days[1:]])
    tot = r.sum()
    return float(r[on].sum() / tot) if tot > 0 else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
    ap.add_argument("--max-symbols", type=int, default=None, help="smoke test on a slice (don't read results)")
    a = ap.parse_args(argv)
    hist = earnings_history.load()
    st = earnings_history.status(hist)
    if not st.get("complete"):
        raise SystemExit(f"earnings history is not complete: {st}")
    spy_b = pb.bars_from_series(_br.load_ohlcv("SPY"))
    spy_days = sorted(spy_b["idx"], key=spy_b["idx"].get)
    spy = dict(zip(spy_days, spy_b["c"], strict=True))
    syms = sorted(hist["symbols"])
    if a.max_symbols:
        syms = syms[:a.max_symbols]
    rows, rows20, shares, used = [], [], [], 0
    for s in syms:
        ts = _br.load_ohlcv(s)
        b = pb.bars_from_series(ts) if ts else None
        if not b:
            continue
        days = sorted(b["idx"], key=b["idx"].get)
        sessions = {e["first_session"] for e in hist["symbols"][s]["events"] if e.get("first_session")}
        rows += symbol_rows(days, b["c"], spy, sessions, H)
        rows20 += symbol_rows(days, b["c"], spy, sessions, H_CONTEXT)
        if (sh := report_day_share([d for d in days if d >= START], [b["c"][b["idx"][d]] for d in days if d >= START],
                                   sessions)) is not None:
            shares.append(sh)
        used += 1
    diffs, diffs20 = daily_differences(rows), daily_differences(rows20)
    halves = [half_stats(diffs, START, SPLIT), half_stats(diffs, SPLIT, None)]
    v = verdict(halves)
    ctx20 = [half_stats(diffs20, START, SPLIT, H_CONTEXT), half_stats(diffs20, SPLIT, None, H_CONTEXT)]
    rep = {"run_at": datetime.datetime.now().isoformat(timespec="seconds"), "symbols": used, "smoke_test": bool(a.max_symbols),
           "history_built": hist.get("built"), "rows": len(rows),
           "group_counts": {g: sum(r[1] == g for r in rows) for g in ("before", "clean")},
           "big_drop_rate_pct": {g: round(100 * float(np.mean([r[2] for r in rows if r[1] == g])), 3) for g in ("before", "clean")},
           "halves": dict(zip(("2005-2015", "2016-on"), halves, strict=True)), "verdict": v,
           "context_h20": dict(zip(("2005-2015", "2016-on"), ctx20, strict=True)),
           "report_day_share_of_abs_moves_median": round(float(np.median(shares)), 4) if shares else None}
    sys.stdout.write(json.dumps(rep, indent=1) + "\n")
    if not a.max_symbols:
        out = os.path.join(paths.data_dir(), f"earnings_risk_study_{datetime.date.today().isoformat()}.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=1)
        sys.stdout.write(f"wrote {out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
