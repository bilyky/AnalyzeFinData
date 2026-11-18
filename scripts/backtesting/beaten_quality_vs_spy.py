"""
beaten_quality_vs_spy.py — R&D #91b: do beaten-down GOOD companies beat the index (SPY) over the
next 60 sessions, and does the stretch add anything beyond what those stocks do against SPY on
ordinary days?

Raised 2026-11-17 after #91's first run (disclosed: written after seeing #91's good-vs-junk result).
The owner's point: #91 compared good with junk, but the question is good vs the index (current
examples: MU, AMD, INTC). Rules fixed here before this script's full run.

Events and labels: exactly #91's (beaten_quality_study): #89's stock events, label A = EDGAR
point-in-time good/junk/mid (from 2012), label B = Chaikin daily PGR (>= 6 good, <= 2 junk).
Outcomes, from the next close to 60 sessions later:
    vs_spy  = stock return - SPY return over the same dates (pp)
    drift   = that stock's mean vs_spy over ALL its days in the same era (what it does against
              SPY normally; today's survivors beat SPY on ordinary days too)
    stretch = vs_spy - drift (what the stretch adds beyond the stock's normal edge over SPY)
Statistic: monthly means over the GOOD events, Newey-West t lag 2, values winsorized at the pooled
1st/99th percentiles. Halves: A before / from 2019-01-01; B before / from 2024-05-01.

Decision rules (per label A and B; on all events and on SPY-above-200-day events):
    H1 vs_spy:   GOOD_BEATS_SPY if month mean > 0, t >= 1.96 and both halves > 0;
                 GOOD_LAGS_SPY if t <= -1.96; INSUFFICIENT if < 24 months or < 100 events;
                 else INCONCLUSIVE.
    H2 stretch:  STRETCH_ADDS if month mean > 0, t >= 1.96 and both halves > 0;
                 STRETCH_HURTS if t <= -1.96; INSUFFICIENT / INCONCLUSIVE as above.
  H1 answers "did they beat the index"; H2 answers "was that because of the stretch, or would
  these survivors have beaten SPY anyway". Only H2 can support a stretch-buying rule.
Eight verdicts: a lone pass is a lead, not a finding.
Also (descriptive, no verdict): per-symbol history for today's chip names (the screen's
semiconductor names) — events, median vs_spy, share beating SPY.

Not measured: costs, stops, taxes. Biases: survivorship (H1 is inflated by it; H2 removes the
stock's own normal drift, not the bias of who survived), today's ETF labels, Chaikin history may be
recomputed (B).

Usage: AETHER_CACHE_DIR=<main>/Data python scripts/backtesting/beaten_quality_vs_spy.py
"""
import argparse
import datetime
import glob
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "monitoring"))

import beaten_quality_study as bq  # noqa: E402
import setup_s10_gate_study as sg  # noqa: E402
import stretch_regime_study as rs  # noqa: E402
from aether import paths  # noqa: E402

H = 60
LAG = 2
GATE_T = 1.96
MIN_MONTHS, MIN_EVENTS = 24, 100
CHIPS = ("AMAT", "AMD", "ASML", "AVGO", "GFS", "INTC", "KLAC", "LRCX", "MPWR", "MRVL", "MU", "NVDA",
         "ON", "QCOM", "QRVO", "SMCI", "STX", "SWKS", "TER", "TSM", "UMC", "WDC")


def vs_spy_at(b: dict, j: int, spy: dict) -> float | None:
    """Stock minus SPY return (pp) from close j+1 to close j+1+H, or None."""
    c = b["c"]
    if j + 1 + H >= len(c) or c[j + 1] <= 0:
        return None
    d0, d1 = b["days"][j + 1], b["days"][j + 1 + H]
    if d0 not in spy["idx"] or d1 not in spy["idx"]:
        return None
    s0, s1 = spy["c"][spy["idx"][d0]], spy["c"][spy["idx"][d1]]
    if s0 <= 0:
        return None
    return (c[j + 1 + H] / c[j + 1] - (s1 / s0)) * 100


def drift_by_era(b: dict, spy: dict) -> dict:
    """The stock's mean vs_spy over all its days, per era."""
    acc = {}
    for j in range(len(b["c"]) - 1 - H):
        x = vs_spy_at(b, j, spy)
        if x is not None:
            acc.setdefault(rs.era_of(b["days"][j]), []).append(x)
    return {k: sum(v) / len(v) for k, v in acc.items() if k and v}


def monthly(events: list, key: str, bounds=None) -> dict:
    xs = [e[key] for e in events if e.get(key) is not None]
    if bounds is None:
        bounds = (float(np.quantile(xs, 0.01)), float(np.quantile(xs, 0.99))) if xs else (0.0, 0.0)
    by = {}
    for e in events:
        if e.get(key) is not None:
            by.setdefault(e["date"][:7], []).append(max(bounds[0], min(bounds[1], e[key])))
    months = sorted(by)
    means = [float(np.mean(by[m])) for m in months]
    t = sg._t_hac(means, LAG) if len(means) > LAG + 2 else None
    return {"events": len(xs), "months": len(months), "mean": round(float(np.mean(means)), 3) if means else None,
            "t_hac": round(t, 2) if t is not None else None, "beat_share": round(float(np.mean([x > 0 for x in xs])), 3) if xs else None,
            "bounds": bounds}


def verdict(full: dict, h1: dict, h2: dict, pos: str, neg: str) -> str:
    if full["months"] < MIN_MONTHS or full["events"] < MIN_EVENTS or full["t_hac"] is None:
        return "INSUFFICIENT"
    if full["mean"] > 0 and full["t_hac"] >= GATE_T and (h1.get("mean") or 0) > 0 and (h2.get("mean") or 0) > 0:
        return pos
    if full["t_hac"] <= -GATE_T:
        return neg
    return "INCONCLUSIVE"


def judge(good: list, split: str) -> dict:
    out = {}
    for key, pos, neg in (("vs_spy", "GOOD_BEATS_SPY", "GOOD_LAGS_SPY"), ("stretch", "STRETCH_ADDS", "STRETCH_HURTS")):
        full = monthly(good, key)
        h1 = monthly([e for e in good if e["date"] < split], key, full["bounds"])
        h2 = monthly([e for e in good if e["date"] >= split], key, full["bounds"])
        out[key] = {"verdict": verdict(full, h1, h2, pos, neg), "all": full, "before": h1, "from": h2}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="R&D #91b beaten-down good companies vs SPY")
    ap.add_argument("--max-symbols", type=int, default=None, help="smoke test on a slice (don't read results)")
    a = ap.parse_args(argv)
    spy = rs.load_bars("SPY")
    spy_up = rs.spy_regime(spy)
    base, t2c = bq.load_edgar()
    syms = sorted(os.path.basename(p)[:-len("_daily.json")] for p in glob.glob(os.path.join(paths.ohlcv_dir(), "*_daily.json")))
    syms = [s for s in syms if os.path.isdir(os.path.join(paths.symbol_dir(), s))]
    if a.max_symbols:
        syms = syms[:a.max_symbols]
    events = []
    for s in syms:
        b = rs.load_bars(s)
        if not b or len(b["c"]) < rs.MIN_HIST + 260 or rs.is_etf(s):
            continue
        facts, rows = bq.edgar_facts(base, t2c, s), bq.chart_rows(s)
        rdays = sorted(rows)
        drift = drift_by_era(b, spy)
        c = np.asarray(b["c"], float)
        for j in rs.find_events(c):
            if j < 252 or rs.era_of(b["days"][j]) is None:
                continue
            x = vs_spy_at(b, j, spy)
            if x is None:
                continue
            d = b["days"][j]
            dr = drift.get(rs.era_of(d))
            events.append({"symbol": s, "date": d, "vs_spy": x, "stretch": x - dr if dr is not None else None,
                           "spy_above200": spy_up.get(d),
                           "qa": bq.quality_edgar(facts, d) if facts and d >= bq.A_START else None,
                           "qb": bq.quality_chart(rows, d, rdays) if rows else None})
    rep = {"run": datetime.datetime.now().isoformat(timespec="seconds"), "smoke_test": bool(a.max_symbols),
           "symbols": len(syms), "events": len(events), "spy_last": spy["days"][-1]}
    for lab, split in (("qa", bq.A_SPLIT), ("qb", bq.B_SPLIT)):
        good = [e for e in events if e.get(lab) == "good"]
        rep[lab] = {"all": judge(good, split),
                    "spy_above200": judge([e for e in good if e.get("spy_above200") == 1.0], split),
                    "context_all_events_vs_spy": {k: v for k, v in monthly([e for e in events if e.get(lab)], "vs_spy").items() if k != "bounds"}}
    chips = {}
    for s in CHIPS:
        ev = [e for e in events if e["symbol"] == s]
        xs = [e["vs_spy"] for e in ev]
        chips[s] = {"events": len(ev), "median_vs_spy": round(float(np.median(xs)), 2) if xs else None,
                    "beat_share": round(float(np.mean([x > 0 for x in xs])), 2) if xs else None,
                    "last": ev[-1]["date"] if ev else None}
    rep["chips_descriptive"] = chips
    out = os.path.join(paths.data_dir(), f"beaten_quality_vs_spy_{datetime.datetime.now():%Y-%m-%d_%H%M%S}.json")
    os.makedirs(paths.data_dir(), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1, default=str)
    sys.stdout.write(json.dumps({"out": out, "events": len(events),
                                 **{f"{lab}/{reg}/{k}": rep[lab][reg][k]["verdict"] for lab in ("qa", "qb")
                                    for reg in ("all", "spy_above200") for k in ("vs_spy", "stretch")}}, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
