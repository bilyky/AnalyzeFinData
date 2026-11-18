"""
confirm_entry_study.py — R&D #92: after a deep stretch, does waiting for a bottom CONFIRMATION
(detect the bottom, don't predict it) and buying then beat SPY, and beat buying on the stretch day?

Raised 2026-11-17. #89: a lower low follows 83-90% of stretches, so buying the stretch day is
usually early. #91/#91b: neither fundamentals nor the stretch itself gave an edge over SPY. The
owner's idea: detect the bottom after it forms, then focus on those names. Rules fixed here before
the full run.

Events: #89's stock events (stretch_regime_study.find_events on split-adjusted closes). For each
event day j, the first confirmation in the next 20 sessions (j+1..j+20), from production code
called exactly as the live screen calls it (raw cached bars up to that date; no look-ahead):
    vic2b   'Vic2B↑'  in patterns.chart_pattern_score(...)[1]  (failed breakdown: under a prior
                       low, then closes back above it)
    vic123  'Vic123↑' in patterns.chart_pattern_score(...)[1]  (Trader Vic 1-2-3 bottom)
    rsidiv  scoring.rsi_divergence_score(...) > 0               (bullish RSI divergence)
    any     the first of the three
Outcomes (60 sessions, from the NEXT close after the entry day; split-adjusted bars):
    conf_vs_spy  = entry on the confirmation day: stock minus SPY (pp)
    event_vs_spy = entry on the stretch day (#91b's outcome)
    paired       = conf_vs_spy - event_vs_spy, on events that confirmed
Statistic: monthly means (month of the confirmation for H1, of the event for H2), Newey-West t
lag 2, winsorized at the pooled 1st/99th percentiles. Halves: before / from 2014-01-01.

Decision rules, per signal (fixed before the run):
    H1  CONF_BEATS_SPY     conf_vs_spy month mean > 0, t >= 1.96, both halves > 0
        CONF_LAGS_SPY      t <= -1.96
    H2  CONF_BEATS_EVENT   paired month mean > 0, t >= 1.96, both halves > 0
        CONF_WORSE         t <= -1.96
    INSUFFICIENT if < 24 months or < 100 entries; INCONCLUSIVE otherwise.
  Eight verdicts (4 signals x 2): about a 1-in-5 chance that one passes by luck at this threshold.
  H1 is the owner's yardstick (beat the index). H2 asks whether waiting helps at all.
Also reported (descriptive): how often each signal fires, days to it, the price paid vs the
stretch-day close, and the results split by #91's labels (EDGAR good/junk, Chaikin PGR) where the
groups have >= 30 entries.

Not measured: costs, stops, sizing; events that never confirm are not traded (their share is
reported). Biases: today's universe (survivors), detectors read raw (unadjusted) bars as live does,
so a split inside the window can confuse them (rare); Chaikin history may be recomputed.

Usage: AETHER_CACHE_DIR=<main>/Data python scripts/backtesting/confirm_entry_study.py [--workers 6]
"""
import argparse
import datetime
import glob
import json
import multiprocessing
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "monitoring"))

import beaten_quality_study as bq  # noqa: E402
import beaten_quality_vs_spy as vs  # noqa: E402
import setup_s10_gate_study as sg  # noqa: E402
import stretch_regime_study as rs  # noqa: E402
from aether import patterns, paths, scoring  # noqa: E402

WINDOW = 20
SPLIT = "2014-01-01"
LAG = 2
GATE_T = 1.96
MIN_MONTHS, MIN_ENTRIES, MIN_GROUP = 24, 100, 30
SIGNALS = ("vic2b", "vic123", "rsidiv")


def signals_on(ts: dict, day: str) -> set:
    """Which confirmations fire on `day`, from the production detectors (data up to day only)."""
    out = set()
    _score, names = patterns.chart_pattern_score(ts, day)
    if "Vic2B↑" in names:
        out.add("vic2b")
    if "Vic123↑" in names:
        out.add("vic123")
    if scoring.rsi_divergence_score(ts, day) > 0:
        out.add("rsidiv")
    return out


def first_confirmations(days: list, j: int, fire) -> dict:
    """{signal: index of the first day in j+1..j+WINDOW where it fires}; 'any' = earliest."""
    first = {}
    for k in range(j + 1, min(len(days), j + 1 + WINDOW)):
        for s in fire(days[k]):
            first.setdefault(s, k)
        if len(first) == len(SIGNALS):
            break
    if first:
        first["any"] = min(first.values())
    return first


def symbol_rows(args) -> list:
    sym, spy, spy_up, base, t2c = args
    b = rs.load_bars(sym)
    if not b or len(b["c"]) < rs.MIN_HIST + 260 or rs.is_etf(sym):
        return []
    try:
        with open(os.path.join(paths.ohlcv_dir(), f"{sym}_daily.json"), encoding="utf-8") as f:
            ts = json.load(f).get("Time Series (Daily)", {})
    except (OSError, ValueError):
        return []
    facts, rows = bq.edgar_facts(base, t2c, sym), bq.chart_rows(sym)
    rdays = sorted(rows)
    cache = {}

    def fire(day):
        if day not in cache:
            cache[day] = signals_on(ts, day)
        return cache[day]

    out = []
    for j in rs.find_events(np.asarray(b["c"], float)):
        if j < 252 or rs.era_of(b["days"][j]) is None:
            continue
        ev = vs.vs_spy_at(b, j, spy)
        if ev is None:
            continue
        d = b["days"][j]
        row = {"symbol": sym, "date": d, "event_vs_spy": ev, "spy_above200": spy_up.get(d),
               "qa": bq.quality_edgar(facts, d) if facts and d >= bq.A_START else None,
               "qb": bq.quality_chart(rows, d, rdays) if rows else None}
        for s, k in first_confirmations(b["days"], j, fire).items():
            cv = vs.vs_spy_at(b, k, spy)
            if cv is None:
                continue
            row[s] = {"day": b["days"][k], "wait": k - j, "conf_vs_spy": cv, "paired": cv - ev,
                      "price_vs_event": (b["c"][k] / b["c"][j] - 1) * 100,
                      "lower_low_first": float(min(b["l"][j + 1:k + 1]) < b["l"][j])}
        out.append(row)
    return out


def monthly(points: list, bounds=None) -> dict:
    """points: [(date, value)] -> monthly means, Newey-West t (lag 2)."""
    xs = [x for _d, x in points]
    if bounds is None:
        bounds = (float(np.quantile(xs, 0.01)), float(np.quantile(xs, 0.99))) if xs else (0.0, 0.0)
    by = {}
    for d, x in points:
        by.setdefault(d[:7], []).append(max(bounds[0], min(bounds[1], x)))
    months = sorted(by)
    means = [float(np.mean(by[m])) for m in months]
    t = sg._t_hac(means, LAG) if len(means) > LAG + 2 else None
    return {"entries": len(xs), "months": len(months), "mean": round(float(np.mean(means)), 3) if means else None,
            "t_hac": round(t, 2) if t is not None else None,
            "positive_share": round(float(np.mean([x > 0 for x in xs])), 3) if xs else None, "bounds": bounds}


def verdict(full: dict, h1: dict, h2: dict, pos: str, neg: str) -> str:
    if full["months"] < MIN_MONTHS or full["entries"] < MIN_ENTRIES or full["t_hac"] is None:
        return "INSUFFICIENT"
    if full["mean"] > 0 and full["t_hac"] >= GATE_T and (h1.get("mean") or 0) > 0 and (h2.get("mean") or 0) > 0:
        return pos
    if full["t_hac"] <= -GATE_T:
        return neg
    return "INCONCLUSIVE"


def judge(rows: list, sig: str, key: str, date_of: str, pos: str, neg: str) -> dict:
    pts = [((r[sig]["day"] if date_of == "conf" else r["date"]), r[sig][key]) for r in rows if sig in r]
    full = monthly(pts)
    h1 = monthly([p for p in pts if p[0] < SPLIT], full["bounds"])
    h2 = monthly([p for p in pts if p[0] >= SPLIT], full["bounds"])
    return {"verdict": verdict(full, h1, h2, pos, neg), "all": _strip(full), "before": _strip(h1), "from": _strip(h2)}


def _strip(d: dict) -> dict:
    return {k: v for k, v in d.items() if k != "bounds"}


def describe(rows: list, sig: str) -> dict:
    hit = [r[sig] for r in rows if sig in r]
    if not hit:
        return {"fires": 0}
    return {"fires": len(hit), "fire_rate": round(len(hit) / len(rows), 3),
            "median_wait": float(np.median([h["wait"] for h in hit])),
            "median_price_vs_event": round(float(np.median([h["price_vs_event"] for h in hit])), 2),
            "lower_low_before_signal": round(float(np.mean([h["lower_low_first"] for h in hit])), 3),
            "median_conf_vs_spy": round(float(np.median([h["conf_vs_spy"] for h in hit])), 2)}


def by_label(rows: list, sig: str, lab: str) -> dict:
    out = {}
    for g in ("good", "junk"):
        xs = [r[sig]["conf_vs_spy"] for r in rows if sig in r and r.get(lab) == g]
        if len(xs) >= MIN_GROUP:
            out[g] = {"n": len(xs), "mean": round(float(np.mean(xs)), 2), "median": round(float(np.median(xs)), 2),
                      "beat_spy": round(float(np.mean([x > 0 for x in xs])), 3)}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="R&D #92 confirmed-bottom entries vs SPY")
    ap.add_argument("--max-symbols", type=int, default=None, help="smoke test on a slice (don't read results)")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    a = ap.parse_args(argv)
    spy = rs.load_bars("SPY")
    spy_up = rs.spy_regime(spy)
    base, t2c = bq.load_edgar()
    syms = sorted(os.path.basename(p)[:-len("_daily.json")] for p in glob.glob(os.path.join(paths.ohlcv_dir(), "*_daily.json")))
    syms = [s for s in syms if os.path.isdir(os.path.join(paths.symbol_dir(), s))]
    if a.max_symbols:
        syms = syms[:a.max_symbols]
    jobs = [(s, spy, spy_up, base, t2c) for s in syms]
    if a.workers > 1:
        with multiprocessing.Pool(a.workers) as pool:
            parts = pool.map(symbol_rows, jobs, chunksize=4)
    else:
        parts = [symbol_rows(j) for j in jobs]
    rows = [r for p in parts for r in p]
    rep = {"run": datetime.datetime.now().isoformat(timespec="seconds"), "smoke_test": bool(a.max_symbols),
           "symbols": len(syms), "events": len(rows), "spy_last": spy["days"][-1], "signals": {}}
    for sig in SIGNALS + ("any",):
        rep["signals"][sig] = {
            "h1_vs_spy": judge(rows, sig, "conf_vs_spy", "conf", "CONF_BEATS_SPY", "CONF_LAGS_SPY"),
            "h2_vs_event": judge(rows, sig, "paired", "event", "CONF_BEATS_EVENT", "CONF_WORSE"),
            "describe": describe(rows, sig),
            "by_edgar": by_label(rows, sig, "qa"), "by_chart_pgr": by_label(rows, sig, "qb"),
            "spy_above200_vs_spy": monthly([(r[sig]["day"], r[sig]["conf_vs_spy"]) for r in rows
                                            if sig in r and r.get("spy_above200") == 1.0])}
        rep["signals"][sig]["spy_above200_vs_spy"].pop("bounds", None)
    out = os.path.join(paths.data_dir(), f"confirm_entry_study_{datetime.datetime.now():%Y-%m-%d_%H%M%S}.json")
    os.makedirs(paths.data_dir(), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1, default=str)
    sys.stdout.write(json.dumps({"out": out, "events": len(rows),
                                 **{f"{s}/{h}": rep["signals"][s][h]["verdict"] for s in SIGNALS + ("any",)
                                    for h in ("h1_vs_spy", "h2_vs_event")}}, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
