"""
Earnings drift: after a big, heavy-volume reaction to an earnings report, does the stock keep moving the same way?
(R&D #98 step 3, owner order R2, 2026-11-25.) Pre-registered 2026-11-25.

Part A (this script, prices only) runs now. Part B (reading the 8-K press release with an AI model) is registered
below so the order is on record; it runs once its labels exist, in its own script, under these rules.

PART A — PRICE-REACTION DRIFT
  Events: Data/earnings_history.json (SEC 8-K item 2.02), each report's first session d (the first session that
  opens after the release), 2005-01-03 on, US filers with OHLCV.
  Reaction on d: the stock's close-to-close return minus SPY's (split-adjusted closes).
  Heavy volume: volume on d at least VOL_MULT (2) x the mean of the 20 sessions before d (raw volume from the cache).
  UP event: reaction >= +5% and heavy. DOWN event: reaction <= -5% and heavy.
  Holding: from the close of d, the next H = 20 sessions (60 reported). Daily excess = stock daily return minus SPY's.
  Calendar-time portfolio per group: each day, the equal-weight mean daily excess over the group's events being held
  that day (days with fewer than MIN_ACTIVE = 3 held events are skipped). Statistic: mean daily excess, Newey-West
  t with lag 5 (R&D #58), on 2005-2015 and 2016-on separately.
  DRIFT_UP      UP portfolio mean > 0 with t >= 2 in both halves -> a candidate card for the combination study (G-A)
                and the rule shadow log; nothing is wired from this study.
  DRIFT_DOWN    DOWN portfolio mean < 0 with t <= -2 in both halves -> a candidate "avoid / exit after a bad report"
                rule for the shadow log (risk first).
  Both can hold; otherwise INCONCLUSIVE for that side.
  Reported, not judged: H = 60; event counts; mean 20-session excess per event; the reaction-day move itself.

PART B — READING THE RELEASE (registered now, run later)
  Sample: events from 2021-01-01 whose 8-K lists exhibit 99.1 (the press release), fetched from SEC EDGAR through the
  cached client; text cut to the first 6,000 characters.
  Label: one fixed prompt, asked to return JSON {"results": "beat"|"miss"|"inline"|"unclear", "guidance":
  "raised"|"lowered"|"maintained"|"none"|"unclear", "tone": -2..2}. Labels are written to their own file before any
  outcome is read, and are never re-generated after the outcome analysis starts.
  Question: within the UP, DOWN and "all other" reaction groups of Part A, does the label add information? Primary:
  guidance raised vs lowered, difference in 20-session excess return after d, per-date differences, Newey-West t
  lag 19, halves 2021-2023 / 2024-on. LABEL_ADDS if t >= 2 in both halves with the same sign; otherwise
  INCONCLUSIVE. A pass justifies a proposal only (an AI-read catalyst card), never live wiring.

NOT MEASURED / BIASES
  Today's symbol list; foreign filers absent; acceptance time can be later than the release (a pre-open release
  accepted after 09:30 is dated to the next session: the reaction may then be split over two days); raw volume
  around splits; no costs; close-to-close returns.

Usage (reading the real caches from a worktree):
    AETHER_DATA_DIR=<main>/Data AETHER_CACHE_DIR=<main>/Data python scripts/backtesting/earnings_drift_study.py
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

H = 20
H_CONTEXT = 60
START = "2005-01-03"
SPLIT = "2016-01-01"
REACTION = 0.05
VOL_MULT = 2.0
VOL_WIN = 20
MIN_ACTIVE = 3
HAC_LAG = 5
GATE_T = 2.0


def in_session(accepted_et: str | None) -> bool:
    """Accepted between 09:30 and 16:00 New York time (the ISO string earnings_history stores)."""
    hhmm = (accepted_et or "")[11:16]
    return "09:30" <= hhmm < "16:00" if len(hhmm) == 5 else False


def classify(reaction: float, vol_ratio: float | None) -> str | None:
    if vol_ratio is None or vol_ratio < VOL_MULT:
        return None
    if reaction >= REACTION:
        return "UP"
    if reaction <= -REACTION:
        return "DOWN"
    return None


def events_for(days, closes, volumes, spy, sessions, start=START) -> list:
    """(index, group, reaction, vol_ratio) for each report first session with a classified reaction."""
    idx = {d: i for i, d in enumerate(days)}
    out = []
    for s in sorted(sessions):
        i = idx.get(s)
        if i is None or i < VOL_WIN or s < start or days[i - 1] not in spy or s not in spy:
            continue
        if closes[i - 1] <= 0:
            continue
        reaction = (closes[i] / closes[i - 1] - 1) - (spy[s] / spy[days[i - 1]] - 1)
        base = [v for v in volumes[i - VOL_WIN:i] if v and v > 0]
        vol_ratio = volumes[i] / (sum(base) / len(base)) if base and volumes[i] else None
        g = classify(reaction, vol_ratio)
        if g:
            out.append((i, g, reaction, vol_ratio))
    return out


def held_excess(days, closes, spy, i, h) -> dict:
    """{day: daily excess return} for sessions i+1 .. i+h (stops at the data's end or a missing SPY day)."""
    out = {}
    for j in range(i + 1, min(i + h, len(days) - 1) + 1):
        if days[j] not in spy or days[j - 1] not in spy or closes[j - 1] <= 0:
            break
        out[days[j]] = (closes[j] / closes[j - 1] - 1) - (spy[days[j]] / spy[days[j - 1]] - 1)
    return out


def portfolio(daily: dict, min_active: int = MIN_ACTIVE) -> dict:
    """{day: [excess, ...]} -> {day: mean} for days with at least min_active held events."""
    return {d: float(np.mean(v)) for d, v in daily.items() if len(v) >= min_active}


def half_stats(series: dict, lo: str, hi: str | None) -> dict:
    days = sorted(d for d in series if d >= lo and (hi is None or d < hi))
    xs = [series[d] for d in days]
    t = _t_hac(xs, HAC_LAG) if xs else None
    return {"days": len(days), "mean_daily_excess_bps": round(1e4 * float(np.mean(xs)), 3) if xs else None,
            "t": round(t, 2) if t is not None else None}


def side_verdict(halves: list, sign: int) -> bool:
    return all(h["t"] is not None and (h["t"] >= GATE_T if sign > 0 else h["t"] <= -GATE_T) for h in halves)


def verdict(up_halves: list, down_halves: list) -> list:
    out = []
    if side_verdict(up_halves, +1):
        out.append("DRIFT_UP")
    if side_verdict(down_halves, -1):
        out.append("DRIFT_DOWN")
    return out or ["INCONCLUSIVE"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
    ap.add_argument("--max-symbols", type=int, default=None, help="smoke test on a slice (don't read results)")
    ap.add_argument("--exclude-in-session", action="store_true",
                    help="sensitivity (added after the result, #328 review; reported, not judged): drop reports "
                         "accepted 09:30-16:00 ET, where a pre-open release filed late is dated a day late")
    a = ap.parse_args(argv)
    hist = earnings_history.load()
    if not earnings_history.status(hist).get("complete"):
        raise SystemExit("earnings history is not complete")
    spy_b = pb.bars_from_series(_br.load_ohlcv("SPY"))
    spy = dict(zip(sorted(spy_b["idx"], key=spy_b["idx"].get), spy_b["c"], strict=True))
    syms = sorted(hist["symbols"])[:a.max_symbols] if a.max_symbols else sorted(hist["symbols"])
    daily = {h: {"UP": {}, "DOWN": {}} for h in (H, H_CONTEXT)}
    per_event = {"UP": [], "DOWN": []}
    for s in syms:
        ts = _br.load_ohlcv(s)
        b = pb.bars_from_series(ts) if ts else None
        if not b:
            continue
        days = sorted(b["idx"], key=b["idx"].get)
        vols = [float(ts[d].get("5. volume") or 0) for d in days]
        sessions = {e["first_session"] for e in hist["symbols"][s]["events"] if e.get("first_session")
                    and not (a.exclude_in_session and in_session(e.get("accepted_et")))}
        for i, g, reaction, vr in events_for(days, b["c"], vols, spy, sessions):
            for h in (H, H_CONTEXT):
                ex = held_excess(days, b["c"], spy, i, h)
                for d, x in ex.items():
                    daily[h][g].setdefault(d, []).append(x)
                if h == H and len(ex) == H:
                    per_event[g].append({"sym": s, "day": days[i], "reaction": reaction, "vol_ratio": vr,
                                         "excess20": float(np.prod([1 + x for x in ex.values()]) - 1)})
    res = {}
    for h in (H, H_CONTEXT):
        res[h] = {g: [half_stats(portfolio(daily[h][g]), START, SPLIT), half_stats(portfolio(daily[h][g]), SPLIT, None)]
                  for g in ("UP", "DOWN")}
    v = verdict(res[H]["UP"], res[H]["DOWN"])
    rep = {"run_at": datetime.datetime.now().isoformat(timespec="seconds"), "symbols": len(syms),
           "smoke_test": bool(a.max_symbols), "verdict": v,
           "h20": {g: dict(zip(("2005-2015", "2016-on"), res[H][g], strict=True)) for g in ("UP", "DOWN")},
           "h60_context": {g: dict(zip(("2005-2015", "2016-on"), res[H_CONTEXT][g], strict=True)) for g in ("UP", "DOWN")},
           "events": {g: len(per_event[g]) for g in per_event},
           "mean_event_excess20_pct": {g: round(100 * float(np.mean([e["excess20"] for e in per_event[g]])), 3)
                                       if per_event[g] else None for g in per_event},
           "median_event_excess20_pct": {g: round(100 * float(np.median([e["excess20"] for e in per_event[g]])), 3)
                                         if per_event[g] else None for g in per_event},
           "mean_reaction_pct": {g: round(100 * float(np.mean([e["reaction"] for e in per_event[g]])), 2)
                                 if per_event[g] else None for g in per_event}}
    sys.stdout.write(json.dumps(rep, indent=1) + "\n")
    rep["exclude_in_session"] = bool(a.exclude_in_session)
    if not a.max_symbols:
        tag = "_no_in_session" if a.exclude_in_session else ""
        out = os.path.join(paths.data_dir(), f"earnings_drift_study{tag}_{datetime.date.today().isoformat()}.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump({**rep, "per_event": per_event}, f, indent=1)
        sys.stdout.write(f"wrote {out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
