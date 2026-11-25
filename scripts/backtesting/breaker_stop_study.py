"""
Breaker stop tightening: when the circuit breaker fires, does raising holdings' stops to 1x ATR cut big losses,
or does it sell names into the panic that the normal stop would have kept? (R&D #130, plan G1 part 2, owner
2026-11-25.) Pre-registered 2026-11-25.

Why: on 2026-11-25 the PROD game sold ON and CLFD on stops the breaker had tightened on 11-20 / 11-23; their
original stops (57.65 / 23.80) would have held at that morning's prices. R&D #125 tested only the buy freeze
(INCONCLUSIVE); the stop tightening was never measured.

THE LIVE RULE (aether/circuit_breaker.py, replayed here at each close)
  Breaker state at session k: circuit_breaker.breaker_reason(*breaker_measures(SPY closes up to k)) is not None
  (one-day drop <= -2%, or 10-session drawdown <= -5%; no VXX history, as R&D #125).
  While on: for each holding, tight = price - 1 x ATR(14); the stop becomes tight when tight is above the current
  stop and below the price (never lowered). When the breaker turns off, the original stop is restored (no ratchet
  is modelled here, so the original is always the one restored).

EPISODES (as R&D #125, breaker_freeze_study.episodes): the first breaker-on session after 20+ off sessions, SPY
2000 on. Each episode: every stock with OHLCV and a bar on that day (today's list, instruments.is_excluded out).

TWO POLICIES for a holding taken at the episode day's close c0, original stop S0 = c0 - M x ATR, M = 2.5 (the
BALANCED multiple; 1.5 and 3.5 reported for context), over the next H = 20 sessions:
  ORIG    the stop stays at S0.
  TIGHT   the live rule above, applied on every breaker-on session (including the episode day) and undone on
          breaker-off sessions.
  Each session k after the episode day: exit at close(k) if close(k) <= the stop in force (set at the end of the
  previous session), then update the stop for TIGHT from session k's breaker state and close. Otherwise hold
  to close(t0 + H). Outcome: return from c0 to the exit or the horizon close.

MEASURES, per episode (mean over its stocks), then across episodes:
  A  return difference TIGHT - ORIG
  B  share of outcomes at -10% or worse, TIGHT - ORIG (negative = fewer big losses with tightening)
  One-sample t over episodes for each (20-session windows of different episodes do not overlap).

DECISION RULE (risk first, the owner's bar)
  Significant = |t| >= 2 AND the same sign in both halves (episodes before / from 2013-01-01).
  KEEP_TIGHTENING   B significantly < 0 and A not significantly < 0, or A significantly > 0 and B not
                    significantly > 0 -> keep the live rule.
  DROP_TIGHTENING   A significantly < 0 and B not significantly < 0, or B significantly > 0 -> propose removing
                    the tightening (keep the buy freeze) in its own PR, with a PROD shadow period first.
  TRADE_OFF         A significantly < 0 and B significantly < 0 -> report; the owner decides.
  INCONCLUSIVE      otherwise -> no change.

REPORTED, NOT JUDGED
  M = 1.5 and 3.5; how many holdings each policy stops out; for holdings stopped by TIGHT but not by ORIG, their
  return from the exit to the horizon (the rebound given up); the 2025, spring-2026 and November-2026 episodes.

NOT MEASURED / BIASES
  Every stock is treated as held, not the game's picks; the original stop is a fresh ATR stop at the episode day,
  not the entry stop; stops are checked at closes (live checks at 07:00 PT with a live quote); no profit-lock
  ratchet, scale-outs or re-entries; no costs; today's symbol list.

Usage (reading the real caches from a worktree):
    AETHER_CACHE_DIR=<main>/Data python scripts/backtesting/breaker_stop_study.py
"""
import argparse
import datetime
import glob
import json
import math
import os
import statistics as st
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import backtest_ratings as _br
import breaker_freeze_study as bf
import portfolio_backtest as pb
from aether import instruments, paths

H = 20
M_PRIMARY = 2.5
M_CONTEXT = (1.5, 3.5)
BIG_LOSS = -0.10
SPLIT = "2013-01-01"
T_BAR = 2.0


def simulate(closes, atrs, on_flags, m, h=H) -> dict:
    """One holding from closes[0] for h sessions. on_flags[k]: breaker on at session k (k = 0 is the episode day).
    Returns {policy: (return, exit_index or None)} for ORIG and TIGHT."""
    c0, a0 = closes[0], atrs[0]
    s0 = c0 - m * a0
    out = {}
    for policy in ("ORIG", "TIGHT"):
        stop = s0
        if policy == "TIGHT" and on_flags[0]:
            tight = c0 - atrs[0]
            if s0 < tight < c0:
                stop = tight
        exit_k, ret = None, None
        for k in range(1, min(h, len(closes) - 1) + 1):
            if closes[k] <= stop:
                exit_k, ret = k, closes[k] / c0 - 1
                break
            if policy == "TIGHT":
                if on_flags[k]:
                    tight = closes[k] - atrs[k]
                    if stop < tight < closes[k]:
                        stop = tight
                else:
                    stop = s0
        if exit_k is None:
            last = min(h, len(closes) - 1)
            ret = closes[last] / c0 - 1
        out[policy] = (ret, exit_k)
    return out


def t_stat(xs):
    if len(xs) < 2:
        return None
    sd = st.stdev(xs)
    return (sum(xs) / len(xs)) / (sd / math.sqrt(len(xs))) if sd > 0 else None


def sig(stats: dict, key: str) -> int:
    """+1 / -1 when the measure is significant (|t| >= 2 overall, same sign in both halves), else 0."""
    t, halves = stats[key]["t"], [stats[key]["first_half_mean"], stats[key]["second_half_mean"]]
    if t is None or abs(t) < T_BAR or None in halves:
        return 0
    s = 1 if t > 0 else -1
    return s if all((x > 0) if s > 0 else (x < 0) for x in halves) else 0


def verdict(stats: dict) -> str:
    a, b = sig(stats, "A"), sig(stats, "B")
    if a < 0 and b < 0:
        return "TRADE_OFF"
    if (b < 0 and a >= 0) or (a > 0 and b <= 0):
        return "KEEP_TIGHTENING"
    if (a < 0 and b >= 0) or b > 0:
        return "DROP_TIGHTENING"
    return "INCONCLUSIVE"


def summarize(per_episode: list) -> dict:
    out = {}
    for key in ("A", "B"):
        xs = [e[key] for e in per_episode]
        first = [e[key] for e in per_episode if e["date"] < SPLIT]
        second = [e[key] for e in per_episode if e["date"] >= SPLIT]
        out[key] = {"n": len(xs), "mean": sum(xs) / len(xs) if xs else None, "t": t_stat(xs),
                    "first_half_mean": sum(first) / len(first) if first else None,
                    "second_half_mean": sum(second) / len(second) if second else None,
                    "n_first": len(first), "n_second": len(second)}
    return out


def run_episode(day_i, spy_days, on_all, bars, m) -> dict | None:
    """Means over the episode's stocks of A and B, plus counts, for one stop multiple."""
    window = spy_days[day_i:day_i + H + 1]
    flags = on_all[day_i:day_i + H + 1]
    diffs, big_t, big_o, n, stopped_t, stopped_o, given_up = [], 0, 0, 0, 0, 0, []
    for _s, b in bars.items():
        idx = [b["idx"].get(d) for d in window]
        if None in idx:
            continue
        closes = [b["c"][i] for i in idx]
        atrs = [b["atr"][i] for i in idx]
        if closes[0] <= 0 or not atrs[0] or atrs[0] <= 0:
            continue
        r = simulate(closes, atrs, flags, m)
        (rt, kt), (ro, ko) = r["TIGHT"], r["ORIG"]
        diffs.append(rt - ro)
        big_t += rt <= BIG_LOSS
        big_o += ro <= BIG_LOSS
        stopped_t += kt is not None
        stopped_o += ko is not None
        if kt is not None and ko is None:
            given_up.append(closes[-1] / closes[kt] - 1)
        n += 1
    if not n:
        return None
    return {"date": spy_days[day_i], "stocks": n, "A": sum(diffs) / n, "B": (big_t - big_o) / n,
            "stopped_tight": stopped_t, "stopped_orig": stopped_o,
            "given_up_mean": (sum(given_up) / len(given_up)) if given_up else None, "given_up_n": len(given_up)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
    ap.add_argument("--max-symbols", type=int, default=None, help="smoke test on a slice (don't read results)")
    a = ap.parse_args(argv)
    spy_b = pb.bars_from_series(_br.load_ohlcv("SPY"))
    spy_days = sorted(spy_b["idx"], key=spy_b["idx"].get)
    spy_c = list(spy_b["c"])
    start = next(i for i, d in enumerate(spy_days) if d >= "2000-01-01")
    on_all = [bool(bf.on(spy_c, i)) for i in range(len(spy_c))]
    eps = [i for i in bf.episodes(spy_c) if i >= start and i + H < len(spy_days)]
    syms = sorted(os.path.basename(f)[:-len("_daily.json")] for f in glob.glob(os.path.join(_br.OHLCV_DIR, "*_daily.json")))
    syms = [s for s in syms if s != "SPY" and not instruments.is_excluded(s)]
    if a.max_symbols:
        syms = syms[:a.max_symbols]
    bars = {s: b for s in syms if (ts := _br.load_ohlcv(s)) and (b := pb.bars_from_series(ts))}
    results = {}
    for m in (M_PRIMARY, *M_CONTEXT):
        per = [e for i in eps if (e := run_episode(i, spy_days, on_all, bars, m))]
        results[str(m)] = {"episodes": per, "summary": summarize(per)}
    prim = results[str(M_PRIMARY)]
    v = verdict(prim["summary"])
    rep = {"run_at": datetime.datetime.now().isoformat(timespec="seconds"), "symbols": len(bars), "smoke_test": bool(a.max_symbols),
           "range": [spy_days[start], spy_days[-1]], "episodes": len(eps), "verdict": v,
           "primary_m": M_PRIMARY, "summary": prim["summary"],
           "context": {k: r["summary"] for k, r in results.items() if k != str(M_PRIMARY)},
           "by_episode": prim["episodes"]}
    sm = prim["summary"]
    sys.stdout.write(f"Breaker stop study (R&D #130): {len(bars)} symbols, {len(eps)} episodes, M={M_PRIMARY}\n")
    for key, label in (("A", "return TIGHT-ORIG"), ("B", "share <= -10% TIGHT-ORIG")):
        s = sm[key]
        sys.stdout.write(f"  {label}: mean {s['mean']:+.4f} t {s['t']} halves {s['first_half_mean']} / {s['second_half_mean']}\n")
    for k, r in rep["context"].items():
        sys.stdout.write(f"  context M={k}: A {r['A']['mean']:+.4f} (t {r['A']['t']}), B {r['B']['mean']:+.4f} (t {r['B']['t']})\n")
    sys.stdout.write(f"VERDICT: {v}\n")
    if not a.max_symbols:
        out = os.path.join(paths.data_dir(), f"breaker_stop_study_{datetime.date.today().isoformat()}.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=1, default=str)
        sys.stdout.write(f"wrote {out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
