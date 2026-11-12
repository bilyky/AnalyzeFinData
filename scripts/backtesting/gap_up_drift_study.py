"""
Gap-Up Drift (R&D #84): after a big opening gap up on heavy volume, does the stock keep beating
SPY?

The 2026-10-28 replay (pattern_discovery) found none of the 10 biggest 10-day winners bought, and
the top five all made most of their move in one day: an opening gap of +12..+25% on 3-5x volume,
almost always news (earnings, a deal, a trial result). No price/volume/Chaikin signal predicts the
jump itself. This asks whether the move after it is tradable: buy once the gap is known, hold.

Event: day t with open_t / close_{t-1} - 1 >= GAP_MIN (10%) and volume_t >= VOL_MULT (3x) the
mean volume of the 20 sessions before t; close_{t-1} >= MIN_PRICE ($5); at least 21 prior bars;
not instruments.is_excluded (ETFs, leveraged, crypto); one event per symbol per HORIZON sessions
(the first counts). Prices are split-adjusted (position_watch.bars_from_series, placeholders
dropped); 2023+ events.

Outcome (primary): bought at the NEXT session's open (t+1), the first price that can be traded
after seeing the gap, held to the close of t+HORIZON (20 sessions), minus SPY over the same
window (SPY open_{t+1} to close_{t+20}), minus COST_BPS (20 bps round trip). Winsorized at the
1st/99th percentile of the events. Secondary: 60 sessions; entry at day t's close; gaps >= 20%.

Decision rule (fixed before the first run), on the primary outcome:
    Per calendar month of the event, the mean excess; HAC (Newey-West) t across months, lag 1
    (a 20-session window straddles at most two months).
    DRIFT_SUPPORTED  mean > 0 AND HAC t >= 1.96 AND >= MIN_MONTHS (24) months AND >= MIN_EVENTS
                     (100) events AND the monthly mean is positive in both halves (2023-24 and
                     2025-26) -> propose a small, stopped "buy after a news gap" sleeve, as its own
                     study on the portfolio backtest first. Never wired from this study.
    REVERSAL         mean < 0 AND HAC t <= -1.96 (same month and event minimums).
    INCONCLUSIVE     otherwise.
The secondaries use the same statistic (60 sessions: lag 3) and are reported, not decisive:
four looks, so one |t| ~ 2 among them is weak evidence.

Not measured: stops, sizing, the cause of the gap (no news or earnings data: the gap stands in for
it), slippage beyond the cost assumption, liquidity. Biases: today's universe (survivorship: names
that gapped up and later died are under-represented), split adjustment from the cache.

Usage (from a worktree, reading the real caches):
    AETHER_CACHE_DIR=<main>/Data python scripts/backtesting/gap_up_drift_study.py
"""
import datetime
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "monitoring"))

import position_watch as pw  # noqa: E402
import setup_s10_gate_study as sg  # noqa: E402
from aether import instruments, paths  # noqa: E402

GAP_MIN = 0.10
BIG_GAP = 0.20
VOL_MULT = 3.0
VOL_WIN = 20
MIN_PRICE = 5.0
HORIZON = 20
LONG = 60
COST_BPS = 20.0
MIN_YEAR = "2023"
SPLIT = "2025-01-01"
GATE_T = 1.96
MIN_MONTHS = 24
MIN_EVENTS = 100


def find_events(b: dict, spacing: int = HORIZON) -> list:
    """Indices t of gap-up events in bars `b` (see the module docstring), forward in time."""
    out, last = [], None
    for t in range(VOL_WIN + 1, len(b["c"])):
        prev = b["c"][t - 1]
        if prev < MIN_PRICE or b["o"][t] / prev - 1 < GAP_MIN:
            continue
        base = sum(b["v"][t - VOL_WIN:t]) / VOL_WIN
        if base <= 0 or b["v"][t] < VOL_MULT * base:
            continue
        if last is not None and t - last < spacing:
            continue
        out.append(t)
        last = t
    return out


def window_return(b: dict, start: int, end: int, at_open: bool = True) -> float | None:
    """Return (%) from the open (or close) of bar `start` to the close of bar `end`."""
    if start >= len(b["c"]) or end >= len(b["c"]):
        return None
    p0 = b["o"][start] if at_open else b["c"][start]
    return (b["c"][end] / p0 - 1) * 100 if p0 > 0 else None


def excess(b, spy, t, horizon, at_open=True):
    """Stock minus SPY over the same dates, minus costs. Entry: open of t+1 (or close of t)."""
    start = t + 1 if at_open else t
    end = t + horizon
    if end >= len(b["c"]):
        return None
    d0, d1 = b["days"][start], b["days"][end]
    if d0 not in spy["idx"] or d1 not in spy["idx"]:
        return None
    s = window_return(b, start, end, at_open)
    m = window_return(spy, spy["idx"][d0], spy["idx"][d1], at_open)
    return None if s is None or m is None else s - m - COST_BPS / 100


def monthly_test(events: list, key: str, lag: int, bounds) -> dict:
    by = {}
    for e in events:
        x = e.get(key)
        if x is None:
            continue
        by.setdefault(e["date"][:7], []).append(max(bounds[0], min(bounds[1], x)))
    months = sorted(by)
    means = [sg._mean(by[m]) for m in months]
    t_hac, t = sg._t_hac(means, lag), sg._t_one_sample(means)
    return {"events": sum(len(v) for v in by.values()), "months": len(months),
            "month_mean": round(sg._mean(means), 3) if means else None,
            "t": round(t, 2) if t is not None else None, "t_hac": round(t_hac, 2) if t_hac is not None else None}


def verdict(primary: dict, half1: dict, half2: dict) -> str:
    m, t = primary.get("month_mean"), primary.get("t_hac")
    if m is None or t is None or primary["months"] < MIN_MONTHS or primary["events"] < MIN_EVENTS:
        return "INCONCLUSIVE"
    if m > 0 and t >= GATE_T and (half1.get("month_mean") or 0) > 0 and (half2.get("month_mean") or 0) > 0:
        return "DRIFT_SUPPORTED"
    if m < 0 and t <= -GATE_T:
        return "REVERSAL"
    return "INCONCLUSIVE"


def _q(xs, q):
    s = sorted(xs)
    return s[min(len(s) - 1, max(0, int(q * (len(s) - 1))))] if s else None


def summarize(events: list) -> dict:
    xs = [e["x20"] for e in events if e.get("x20") is not None]
    bounds = (_q(xs, 0.01), _q(xs, 0.99)) if xs else (0, 0)
    primary = monthly_test(events, "x20", 1, bounds)
    h1 = monthly_test([e for e in events if e["date"] < SPLIT], "x20", 1, bounds)
    h2 = monthly_test([e for e in events if e["date"] >= SPLIT], "x20", 1, bounds)
    x60 = [e["x60"] for e in events if e.get("x60") is not None]
    b60 = (_q(x60, 0.01), _q(x60, 0.99)) if x60 else (0, 0)
    xc = [e["x20_close"] for e in events if e.get("x20_close") is not None]
    bc = (_q(xc, 0.01), _q(xc, 0.99)) if xc else (0, 0)
    return {
        "events": len(events), "symbols": len({e["symbol"] for e in events}),
        "first": min((e["date"] for e in events), default=None), "last": max((e["date"] for e in events), default=None),
        "x20_dist": {"n": len(xs), "mean": round(sg._mean(xs), 2) if xs else None, "median": _q(xs, 0.5),
                     "beat_spy": round(sum(x > 0 for x in xs) / len(xs), 3) if xs else None,
                     "p01": bounds[0], "p99": bounds[1], "min": min(xs, default=None), "max": max(xs, default=None)},
        "primary": primary, "half_2023_24": h1, "half_2025_26": h2, "verdict": verdict(primary, h1, h2),
        "secondary": {
            "x60": monthly_test(events, "x60", 3, b60),
            "x20_close_entry": monthly_test(events, "x20_close", 1, bc),
            "x20_big_gap": monthly_test([e for e in events if e["gap"] >= BIG_GAP * 100], "x20", 1, bounds),
        },
    }


def collect(symbols, spy) -> list:
    events = []
    for s in symbols:
        try:
            with open(os.path.join(paths.ohlcv_dir(), f"{s}_daily.json"), encoding="utf-8") as f:
                b = pw.bars_from_series(json.load(f).get("Time Series (Daily)", {}))
        except (OSError, ValueError):
            continue
        if not b:
            continue
        for t in find_events(b):
            day = b["days"][t]
            if day < MIN_YEAR:
                continue
            events.append({"symbol": s, "date": day, "gap": round((b["o"][t] / b["c"][t - 1] - 1) * 100, 2),
                           "vol_x": round(b["v"][t] / (sum(b["v"][t - VOL_WIN:t]) / VOL_WIN), 2),
                           "x20": excess(b, spy, t, HORIZON), "x60": excess(b, spy, t, LONG),
                           "x20_close": excess(b, spy, t, HORIZON, at_open=False)})
    return events


def main() -> int:
    files = glob.glob(os.path.join(paths.ohlcv_dir(), "*_daily.json"))
    symbols = sorted(os.path.basename(f)[:-len("_daily.json")] for f in files)
    symbols = [s for s in symbols if s != "SPY" and not instruments.is_excluded(s)]
    with open(os.path.join(paths.ohlcv_dir(), "SPY_daily.json"), encoding="utf-8") as f:
        spy = pw.bars_from_series(json.load(f)["Time Series (Daily)"])
    events = collect(symbols, spy)
    rep = summarize(events)
    rep.update({"run": datetime.datetime.now().isoformat(timespec="seconds"), "universe": len(symbols),
                "params": {"gap_min": GAP_MIN, "vol_mult": VOL_MULT, "min_price": MIN_PRICE, "horizon": HORIZON,
                           "cost_bps": COST_BPS, "split": SPLIT}, "events_list": events})
    out = os.path.join(paths.data_dir(), f"gap_up_drift_study_{datetime.datetime.now():%Y-%m-%d_%H%M%S}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1)
    show = {k: v for k, v in rep.items() if k not in ("events_list",)}
    sys.stdout.write(json.dumps(show, indent=1) + f"\nwrote {out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
