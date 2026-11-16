"""
Last-3-Setups Predictor: does a name's record on its last 3 setups predict its next one?

CLAUDE.md asks, before any BUY, to check the last 3 times a similar setup occurred for the
symbol. The 2026-11-10 winners shortlist used "beat SPY in >= 2 of the last 3" (TMO, ULTA, GCT
were 3/3). But across the 63 names checked that day a setup beat SPY over 20 sessions only 38%
of the time, so 3/3 by chance is ~5% per name, about 3 names: what the check found. This study
asks whether the record carries information at all.

Setup event: a day on which the symbol is eligible (setup_gate_horizon_study.eligible: S10 >=
floor and S10+L60 >= the BALANCED minimum, scores rebuilt by backtest_ratings.compute_br from the
saved Chaikin snapshots) AND Setup = 1 (backtest_ratings.compute_setup_ok, the live rule). Events
of one symbol are thinned forward in time to be >= 20 sessions apart, so their 20-session windows
don't overlap and each earlier outcome is known by the next event's date (no look-ahead).

Outcome: the 20-session return from the event day minus SPY's over the same sessions (excess, %),
winsorized at the 1st/99th percentile of all scored events.

Prior record: how many of the symbol's previous 3 events had excess > 0 (0..3); events with fewer
than 3 previous events are not scored.

Decision rule (fixed before running):
    Primary   HIGH (prior >= 2, the rule used on 11-10) vs LOW (prior <= 1). Per calendar month,
              mean(HIGH) - mean(LOW) over that month's events, on months with both groups;
              Newey-West (HAC) t across months, lag 1 (20-session windows can straddle months).
    RULE_PREDICTS  mean diff > 0 AND HAC t >= 1.96 AND >= 12 months -> keep the check as a filter
                   (still a proposal: any live use is its own PR).
    RULE_MISLEADS  mean diff < 0 AND HAC t <= -1.96 -> the record points the wrong way.
    INCONCLUSIVE   otherwise -> the last-3 record is not evidence for picking; don't select on it.
Secondary (same statistic and thresholds, reported, judged separately): 3/3 vs the rest; Money
Flow Strong vs not Strong at the event (all events with a known prior); the primary per year.
Counted against chance: at |t| >= 1.96 about 1 test in 20 passes by luck, and this runs 3.

Not measured: stops, sizing, costs, and the replacement a skipped name would have made. Biases:
today's universe (symbols with snapshots and price files now), snapshots after the 2026-11-04
quarantine (R&D #74), SPY close-to-close vs the snapshot's price on the event day.

Usage:
    python scripts/backtesting/last3_setups_study.py [--min-year 2023] [--data-dir PATH] [--workers 8]
"""
import argparse
import datetime
import glob
import json
import multiprocessing
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

import backtest_ratings as br  # noqa: E402
import setup_gate_horizon_study as st  # noqa: E402
import setup_s10_gate_study as sg  # noqa: E402
from aether import paths  # noqa: E402

HORIZON = 20          # sessions: the outcome window and the minimum spacing between events
PRIOR_N = 3
GATE_T = 1.96
MIN_MONTHS = 12
LAG = 1

_SPY = {}             # per worker: (dates, {date: close})


def _spy(ohlcv_dir):
    if ohlcv_dir not in _SPY:
        ts = sg._load(ohlcv_dir, "SPY") or {}
        days = sorted(ts)
        _SPY[ohlcv_dir] = (days, {d: float(ts[d]["4. close"]) for d in days})
    return _SPY[ohlcv_dir]


def spy_return(spy, day, n=HORIZON):
    """SPY's n-session return (%) from the last close on or before `day`, or None."""
    days, close = spy
    i = max((k for k, d in enumerate(days) if d <= day), default=None)
    if i is None or i + n >= len(days):
        return None
    return (close[days[i + n]] / close[days[i]] - 1) * 100


def thin(candidates, spacing=HORIZON):
    """Keep events >= spacing sessions apart, forward in time. candidates: [(idx, ...)] sorted."""
    out, last = [], None
    for c in candidates:
        if last is None or c[0] - last >= spacing:
            out.append(c)
            last = c[0]
    return out


def with_prior(events, n=PRIOR_N):
    """Attach to each event the count of its previous n events with excess > 0 (None if < n)."""
    out = []
    for k, e in enumerate(events):
        prev = events[max(0, k - n):k]
        prior = sum(1 for p in prev if p["excess"] > 0) if len(prev) == n else None
        out.append({**e, "prior": prior})
    return out


def _symbol_events(job):
    sym, sym_dir, ohlcv_dir, min_year = job
    ts = sg._load(ohlcv_dir, sym)
    if not ts:
        return []
    spy = _spy(ohlcv_dir)
    dates = sorted(ts)
    pos = {d: i for i, d in enumerate(dates)}
    seas = br.precompute_seasonality(ts)
    cands, prev = [], None
    for path in sorted(glob.glob(os.path.join(sym_dir, sym, f"{sym}_*.json"))):
        day = Path(path).stem[len(sym) + 1:]
        if day not in pos:
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            prev = None
            continue
        if data.get("status") == "invalid symbol":
            prev = data
            continue
        meta = (data.get("metaInfo") or [{}])[0]
        cl = data.get("checklist_stocks") or {}
        try:
            price = float(meta.get("Last") or cl.get("lastPrice") or 0)
        except (TypeError, ValueError):
            price = 0.0
        i = pos[day]
        close = float(ts[day].get("4. close", 0))
        if day < str(min_year) or price <= 0 or i < 20 or i + HORIZON >= len(dates) or (
                close > 0 and abs(price - close) / close > 0.1):
            prev = data
            continue
        res = br.compute_br(data, prev, price, i, dates, ts, seas)
        prev = data
        if not (st.eligible(res[1], res[2]) and br.compute_setup_ok(price, i, dates, ts)):
            continue
        m = spy_return(spy, day)
        if m is None:
            continue
        f20 = (float(ts[dates[i + HORIZON]]["4. close"]) / price - 1) * 100
        cands.append((i, day, f20 - m, str(cl.get("moneyFlow", "") or "").strip()))
    events = [{"symbol": sym, "date": d, "excess": x, "mf": mf} for _, d, x, mf in thin(cands)]
    return with_prior(events)


def events(sym_dir, ohlcv_dir, min_year, workers=1):
    syms = sorted({Path(p).name.replace("_daily.json", "") for p in glob.glob(os.path.join(ohlcv_dir, "*_daily.json"))}
                  & {d for d in os.listdir(sym_dir) if os.path.isdir(os.path.join(sym_dir, d))})
    out = []
    with multiprocessing.Pool(max(1, workers)) as pool:
        for n, rows in enumerate(pool.imap_unordered(_symbol_events, [(s, sym_dir, ohlcv_dir, min_year) for s in syms],
                                                     chunksize=4), 1):
            out.extend(rows)
            if n % 100 == 0 or n == len(syms):
                sys.stderr.write(f"  {n}/{len(syms)} symbols, {len(out)} events\n")
    return out


def monthly_test(evts, in_a, in_b, bounds) -> dict:
    """Per month mean(A) - mean(B) on months with both; HAC t across months."""
    by = {}
    for e in evts:
        g = "A" if in_a(e) else ("B" if in_b(e) else None)
        if g is None:
            continue
        r = max(bounds[0], min(bounds[1], e["excess"]))
        by.setdefault(e["date"][:7], {}).setdefault(g, []).append(r)
    months = sorted(m for m, g in by.items() if g.get("A") and g.get("B"))
    diffs = [sg._mean(by[m]["A"]) - sg._mean(by[m]["B"]) for m in months]
    t_hac, t = sg._t_hac(diffs, LAG), sg._t_one_sample(diffs)
    na = sum(len(by[m]["A"]) for m in months)
    nb = sum(len(by[m]["B"]) for m in months)
    return {"months": len(diffs), "n_a": na, "n_b": nb,
            "mean_diff": round(sg._mean(diffs), 3) if diffs else None,
            "t": round(t, 2) if t is not None else None, "t_hac": round(t_hac, 2) if t_hac is not None else None}


def verdict(test: dict) -> str:
    m, t = test.get("mean_diff"), test.get("t_hac")
    if m is None or t is None or test.get("months", 0) < MIN_MONTHS:
        return "INCONCLUSIVE"
    if m > 0 and t >= GATE_T:
        return "RULE_PREDICTS"
    if m < 0 and t <= -GATE_T:
        return "RULE_MISLEADS"
    return "INCONCLUSIVE"


def group_table(evts) -> dict:
    out = {}
    for k in (0, 1, 2, 3):
        xs = sorted(e["excess"] for e in evts if e["prior"] == k)
        out[str(k)] = {"n": len(xs), "mean": round(sg._mean(xs), 2) if xs else None,
                       "median": round(st._quantile(xs, 0.5), 2) if xs else None,
                       "beat_spy": round(sum(x > 0 for x in xs) / len(xs), 3) if xs else None}
    return out


def summarize(evts) -> dict:
    scored = [e for e in evts if e["prior"] is not None]
    xs = [e["excess"] for e in scored]
    bounds = (st._quantile(xs, 0.01), st._quantile(xs, 0.99)) if xs else (0, 0)
    primary = monthly_test(scored, lambda e: e["prior"] >= 2, lambda e: e["prior"] <= 1, bounds)
    three = monthly_test(scored, lambda e: e["prior"] == 3, lambda e: e["prior"] < 3, bounds)
    mf = monthly_test(scored, lambda e: e["mf"] == "Strong", lambda e: e["mf"] != "Strong", bounds)
    years = sorted({e["date"][:4] for e in scored})
    per_year = {y: monthly_test([e for e in scored if e["date"].startswith(y)], lambda e: e["prior"] >= 2,
                                lambda e: e["prior"] <= 1, bounds) for y in years}
    return {"events": len(evts), "scored": len(scored), "symbols": len({e["symbol"] for e in evts}),
            "first": min((e["date"] for e in scored), default=None),
            "last": max((e["date"] for e in scored), default=None),
            "base_beat_spy": round(sum(x > 0 for x in xs) / len(xs), 3) if xs else None,
            "winsor_bounds": [round(b, 2) for b in bounds], "by_prior": group_table(scored),
            "primary": primary, "verdict": verdict(primary),
            "three_vs_rest": three, "verdict_three": verdict(three),
            "money_flow_strong": mf, "verdict_mf": verdict(mf), "per_year": per_year}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--min-year", type=int, default=2023)
    ap.add_argument("--data-dir", default=paths.data_dir())
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args(argv)
    sym_dir, ohlcv_dir = os.path.join(a.data_dir, "Symbol"), os.path.join(a.data_dir, "Symbol_full")
    rep = summarize(events(sym_dir, ohlcv_dir, a.min_year, a.workers))
    rep["run"] = datetime.datetime.now().isoformat(timespec="seconds")
    out = os.path.join(a.data_dir, f"last3_setups_study_{datetime.datetime.now():%Y-%m-%d_%H%M%S}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1)
    sys.stdout.write(json.dumps({k: rep[k] for k in rep if k != "per_year"}, indent=1) + "\n")
    sys.stdout.write("per year: " + json.dumps({y: (v["mean_diff"], v["t_hac"], v["months"])
                                                for y, v in rep["per_year"].items()}) + f"\nwrote {out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
