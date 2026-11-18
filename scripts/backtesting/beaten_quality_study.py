"""
beaten_quality_study.py — R&D #91: among stocks in a deep short-term stretch (#89's events), do
the good companies snap back more than the junk, and are the big winners there?

Raised 2026-11-17. #89 found that a stretched stock's snap-back is small when SPY is above its
200-day average (+0.23 pp over normal at 20 sessions). The owner asked: what if we only take the
beaten-down GOOD companies and look for the winners there? Rules fixed here before the full run.

Events: #89's stock events (stretch_regime_study.find_events: RSI-14 <= 30 and d20 at the symbol's
own 5th percentile, >= 20 sessions apart), stocks only (no ETF label in the snapshot).

Two definitions of "good", each tested on its own:
  A. EDGAR fundamentals, point-in-time (only facts whose period had ended and that were FILED by
     the event date; aether.ai_buildout._known_by): latest annual net income (a ~1-year
     NetIncomeLoss period, 330-400 days, most recent filing per period end) and the latest
     quarter's revenue YoY (ai_buildout.revenue_yoy). The annual period must have ended within
     550 days and the quarter within 200 days of the event, else unknown.
         good = net income > 0 AND revenue YoY > 0
         junk = net income <= 0 AND revenue YoY <= 0       (anything else: middle, not tested)
     Read straight from Data/edgar_cache (no network). Window: events from 2012-01-01 (the median
     company's first annual filing in the cache is 2011-09).
  B. Chaikin's daily PGR history (Data/Symbol_chart, dailyPgr 1-7, last row on or before the event
     day, within 5 days). The scale is inferred from a cross-tab with the snapshots' 1-5 rating
     (chart 6 sits mostly against snapshot 4 = Bullish; chart 1-2 against snapshot 1-2):
         good = dailyPgr >= 6        junk = 1 <= dailyPgr <= 2
     Window: the history's span (2021-11 to 2026-11). Unknown: whether Chaikin's history is the
     rating shown on each day or recomputed later (a look-ahead risk; reported, not fixable here).

Outcomes, from the next close (no shared price with the event close):
    ex60 = 60-session return minus the symbol's normal 60-session return in that era (pp). PRIMARY.
    ex20 = the same over 20 sessions (comparable with #89).
    win60 = 1 if the 60-session return >= +20% (a "big winner"); reported.
Statistic: per calendar month with both groups, mean(good) - mean(junk) of ex60 (winsorized at the
pooled 1st/99th percentiles); Newey-West t over months, lag 2 (60-session windows overlap ~3 months).

Decision rule, per definition (fixed before the run):
    INSUFFICIENT   < 24 months with both groups, or < 100 events in either group.
    QUALITY_HELPS  t >= 1.96 AND the monthly difference is positive in both halves
                   (A: before / from 2019-01-01; B: before / from 2024-05-01).
    QUALITY_HURTS  t <= -1.96.
    INCONCLUSIVE   otherwise.
  The same rule on the events with SPY above its 200-day mean (today's regime) gives a second
  verdict per definition. Four verdicts in all: about a 1-in-10 chance that one passes by luck at
  this threshold, so a lone pass is a lead, not a finding.
Also reported: big-winner shares, medians, Money Flow (CMF-20 from bars,
ai_buildout.chaikin_money_flow) > 0 vs < 0 on the same events (descriptive), and today's stretched
stocks with their A/B labels (a candidate list for review, not a recommendation).

Not measured: costs, stops, sizing, taxes. Biases: today's universe (survivors; junk that went
bankrupt is missing, which flatters junk); EDGAR covers 473 of 617 screen symbols (some live US
stocks were never fetched); chart history may be recomputed (B); restated facts filed later are
excluded by the filing-date filter, as they should be.

Usage (from a worktree, reading the real caches):
    AETHER_CACHE_DIR=<main>/Data python scripts/backtesting/beaten_quality_study.py
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

import setup_s10_gate_study as sg  # noqa: E402
import stretch_regime_study as rs  # noqa: E402
from aether import ai_buildout as ab  # noqa: E402
from aether import paths  # noqa: E402

H60, H20 = 60, 20
LAG60, LAG20 = 2, 1
BIG_WIN = 0.20
A_START, A_SPLIT = "2012-01-01", "2019-01-01"
B_SPLIT = "2024-05-01"
ANNUAL_DAYS = (330, 400)
ANNUAL_MAX_AGE, QUARTER_MAX_AGE = 550, 200
CHART_MAX_GAP = 5
GOOD_PGR, JUNK_PGR = 6, 2
GATE_T = 1.96
MIN_MONTHS, MIN_EVENTS = 24, 100


def _days(a: str, b: str) -> int:
    return (datetime.date.fromisoformat(b) - datetime.date.fromisoformat(a)).days


def annual_net_income(facts: dict, as_of: str):
    """(period end, value) of the latest ~1-year NetIncomeLoss known by as_of, or (None, None)."""
    entries = (facts or {}).get("facts", {}).get("us-gaap", {}).get("NetIncomeLoss", {}).get("units", {}).get("USD", [])
    annual = [e for e in entries if "start" in e and ab._known_by(e, as_of)
              and ANNUAL_DAYS[0] <= _days(e["start"], e["end"]) <= ANNUAL_DAYS[1]]
    if not annual:
        return None, None
    by_end = ab._latest_per_end(annual)
    end = max(by_end)
    return end, by_end[end]["val"]


def quality_edgar(facts: dict, as_of: str) -> str | None:
    """'good' / 'junk' / 'mid', or None when the facts are missing or stale at as_of."""
    end, ni = annual_net_income(facts, as_of)
    qend, yoy = ab.revenue_yoy(facts, as_of)
    if end is None or qend is None or yoy is None:
        return None
    if _days(end, as_of) > ANNUAL_MAX_AGE or _days(qend, as_of) > QUARTER_MAX_AGE:
        return None
    if ni > 0 and yoy > 0:
        return "good"
    if ni <= 0 and yoy <= 0:
        return "junk"
    return "mid"


def quality_chart(rows: dict, day: str, sorted_days: list | None = None) -> str | None:
    """'good' / 'junk' / 'mid' from the last dailyPgr on or before day (within 5 days), or None."""
    ds = sorted_days if sorted_days is not None else sorted(rows)
    i = int(np.searchsorted(ds, day, side="right")) - 1
    if i < 0 or _days(ds[i], day) > CHART_MAX_GAP:
        return None
    v = rows[ds[i]].get("dailyPgr")
    if not isinstance(v, (int, float)) or v < 1:
        return None
    if v >= GOOD_PGR:
        return "good"
    if v <= JUNK_PGR:
        return "junk"
    return "mid"


def era_base_h(b: dict, h: int) -> dict:
    c, days, acc = b["c"], b["days"], {}
    for j in range(len(c) - 1 - h):
        if c[j + 1] > 0:
            acc.setdefault(rs.era_of(days[j]), []).append(c[j + 1 + h] / c[j + 1] - 1)
    return {k: sum(v) / len(v) for k, v in acc.items() if k and v}


def outcome60(b: dict, j: int, base60: dict) -> dict | None:
    c = b["c"]
    if j + 1 + H60 >= len(c) or c[j + 1] <= 0:
        return None
    r = c[j + 1 + H60] / c[j + 1] - 1
    era = rs.era_of(b["days"][j])
    return {"r60": r * 100, "ex60": (r - base60[era]) * 100 if base60.get(era) is not None else None,
            "win60": 1.0 if r >= BIG_WIN else 0.0}


def cmf_at(b: dict, j: int):
    lo = max(0, j - 19)
    bars = [(b["days"][k], b["h"][k], b["l"][k], b["c"][k], b["v"][k]) for k in range(lo, j + 1) if b["h"][k] > b["l"][k]]
    return ab.chaikin_money_flow(bars, 20)


def monthly_diff(events: list, label: str, key: str, lag: int, bounds=None) -> dict:
    """Per month with both groups: mean(good) - mean(junk) of key; Newey-West t over months."""
    xs = [e[key] for e in events if e.get(key) is not None and e.get(label) in ("good", "junk")]
    if bounds is None:
        bounds = (float(np.quantile(xs, 0.01)), float(np.quantile(xs, 0.99))) if xs else (0.0, 0.0)
    by = {}
    for e in events:
        g, x = e.get(label), e.get(key)
        if g in ("good", "junk") and x is not None:
            by.setdefault(e["date"][:7], {"good": [], "junk": []})[g].append(max(bounds[0], min(bounds[1], x)))
    months = sorted(m for m, v in by.items() if v["good"] and v["junk"])
    diffs = [float(np.mean(by[m]["good"]) - np.mean(by[m]["junk"])) for m in months]
    t = sg._t_hac(diffs, lag) if len(diffs) > lag + 2 else None
    n_good = sum(len(by[m]["good"]) for m in by)
    n_junk = sum(len(by[m]["junk"]) for m in by)
    return {"months": len(months), "n_good": n_good, "n_junk": n_junk,
            "diff": round(float(np.mean(diffs)), 3) if diffs else None,
            "t_hac": round(t, 2) if t is not None else None, "bounds": bounds}


def verdict(full: dict, half1: dict, half2: dict) -> str:
    if full["months"] < MIN_MONTHS or full["n_good"] < MIN_EVENTS or full["n_junk"] < MIN_EVENTS \
            or full["t_hac"] is None:
        return "INSUFFICIENT"
    if full["t_hac"] >= GATE_T and (half1.get("diff") or 0) > 0 and (half2.get("diff") or 0) > 0:
        return "QUALITY_HELPS"
    if full["t_hac"] <= -GATE_T:
        return "QUALITY_HURTS"
    return "INCONCLUSIVE"


def judge(events: list, label: str, split: str) -> dict:
    full = monthly_diff(events, label, "ex60", LAG60)
    h1 = monthly_diff([e for e in events if e["date"] < split], label, "ex60", LAG60, full["bounds"])
    h2 = monthly_diff([e for e in events if e["date"] >= split], label, "ex60", LAG60, full["bounds"])
    return {"verdict": verdict(full, h1, h2), "ex60": full, "before_split": h1, "from_split": h2,
            "ex20": monthly_diff(events, label, "ex20", LAG20)}


def describe(events: list, label: str) -> dict:
    out = {}
    for g in ("good", "mid", "junk"):
        ev = [e for e in events if e.get(label) == g]
        r = [e["r60"] for e in ev if e.get("r60") is not None]
        out[g] = {"events": len(ev), "median_r60": round(float(np.median(r)), 2) if r else None,
                  "big_winner_share": round(float(np.mean([e["win60"] for e in ev if e.get("win60") is not None])), 3) if r else None,
                  "lower_low": round(float(np.mean([e["ll"] for e in ev])), 3) if ev else None}
    return out


def load_edgar() -> tuple:
    base = os.path.join(paths.cache_dir(), "edgar_cache")                 # read only
    with open(os.path.join(base, "company_tickers.json"), encoding="utf-8") as f:
        m = json.load(f)
    rows = m.values() if isinstance(m, dict) else m
    t2c = {str(r["ticker"]).upper(): int(r["cik_str"]) for r in rows if isinstance(r, dict) and "ticker" in r}
    return base, t2c


def edgar_facts(base: str, t2c: dict, sym: str):
    cik = t2c.get(sym)
    p = os.path.join(base, f"facts_{cik}.json") if cik else None
    if not p or not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def chart_rows(sym: str) -> dict:
    p = os.path.join(paths.cache_dir(), "Symbol_chart", f"{sym}_chart.json")
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f).get("rows") or {}
    except (OSError, ValueError):
        return {}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="R&D #91 beaten-down quality")
    ap.add_argument("--max-symbols", type=int, default=None, help="smoke test on a slice (don't read results)")
    a = ap.parse_args(argv)
    spy = rs.load_bars("SPY")
    spy_up = rs.spy_regime(spy)
    base, t2c = load_edgar()
    syms = sorted(os.path.basename(p)[:-len("_daily.json")] for p in glob.glob(os.path.join(paths.ohlcv_dir(), "*_daily.json")))
    syms = [s for s in syms if os.path.isdir(os.path.join(paths.symbol_dir(), s))]
    if a.max_symbols:
        syms = syms[:a.max_symbols]
    events, today = [], []
    for s in syms:
        b = rs.load_bars(s)
        if not b or len(b["c"]) < rs.MIN_HIST + 260 or rs.is_etf(s):
            continue
        facts, rows = edgar_facts(base, t2c, s), chart_rows(s)
        rdays = sorted(rows)
        base60 = era_base_h(b, H60)
        for e in rs.symbol_events(s, b, spy, spy_up):
            j = b["idx"][e["date"]]
            o = outcome60(b, j, base60)
            if o is None:
                continue
            e.update(o)
            e["qa"] = quality_edgar(facts, e["date"]) if facts and e["date"] >= A_START else None
            e["qb"] = quality_chart(rows, e["date"], rdays) if rows else None
            cmf = cmf_at(b, j)
            e["mf"] = None if cmf is None else ("good" if cmf > 0 else "junk")
            events.append(e)
        c = np.asarray(b["c"], float)
        ev = rs.find_events(c)
        if ev and ev[-1] == len(c) - 1:
            d = b["days"][-1]
            today.append({"symbol": s, "date": d, "qa": quality_edgar(facts, d) if facts else None,
                          "qb": quality_chart(rows, d, rdays) if rows else None})
    bull = [e for e in events if e.get("spy_above200") == 1.0]
    rep = {"run": datetime.datetime.now().isoformat(timespec="seconds"), "smoke_test": bool(a.max_symbols),
           "symbols": len(syms), "events": len(events), "spy_last": spy["days"][-1],
           "A_edgar": {"all": judge([e for e in events if e["qa"]], "qa", A_SPLIT),
                       "spy_above200": judge([e for e in bull if e["qa"]], "qa", A_SPLIT),
                       "groups": describe([e for e in events if e["qa"]], "qa")},
           "B_chart_pgr": {"all": judge([e for e in events if e["qb"]], "qb", B_SPLIT),
                           "spy_above200": judge([e for e in bull if e["qb"]], "qb", B_SPLIT),
                           "groups": describe([e for e in events if e["qb"]], "qb")},
           "money_flow_descriptive": {"diff": monthly_diff(events, "mf", "ex60", LAG60),
                                      "groups": describe(events, "mf")},
           "today_stretched": sorted(today, key=lambda x: x["symbol"])}
    os.makedirs(paths.data_dir(), exist_ok=True)
    out = os.path.join(paths.data_dir(), f"beaten_quality_study_{datetime.datetime.now():%Y-%m-%d_%H%M%S}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1, default=str)
    sys.stdout.write(json.dumps({"out": out, "events": len(events),
                                 "A": {k: rep["A_edgar"][k]["verdict"] for k in ("all", "spy_above200")},
                                 "B": {k: rep["B_chart_pgr"][k]["verdict"] for k in ("all", "spy_above200")}}, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
