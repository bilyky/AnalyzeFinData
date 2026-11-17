"""
Trend-break pattern (R&D #88): after a downtrend of lower highs and lower lows, a bounce that
makes a HIGHER HIGH and then a pullback that holds a HIGHER LOW: does price beat SPY over the next
20 and 30 sessions?

Source of the idea: a newsletter ("Buy GDX Calls", 2026-11-16) that called this setup in GDX the
start of a new rally phase, citing one prior example (July 2026, then +49% in six weeks). One
example is an anecdote; this measures the setup across history.

Swings: confirmed zigzag turns on split-adjusted closes (position_watch.bars_from_series,
placeholders dropped). A swing high is confirmed when the close falls PCT below the running high; a
swing low when the close rises PCT above the running low. The zigzag is
gann_time_cycle_study.zigzag_indices (shared; it also returns the confirmation index).

Event, at the confirmation of a swing low L0 (its zigzag reversal day k, never the low's own day):
    L0 > L1            the higher low
    H0 > H1            the most recent swing high (between L1 and L0) is a higher high
    H1 < H2 < H3       two consecutive lower highs before it
    L1 < L2 < L3       two consecutive lower lows before it (L1, the exhaustion low, the lowest)
Entry at the open of session k+1; exit at the close of k+20 (primary) and k+30. Outcome: the return
minus SPY's over the same dates (SPY open of the entry day to close), minus 10 bps.

Universes: (a) GDX alone (context: too few events to decide); (b) ETFs: symbols whose latest Chaikin
snapshot has a non-blank metaInfo.etf_group_name (instruments has no ETF flag), excluded
leveraged/inverse/crypto names out; (c) stocks: the rest, instruments.is_excluded names out.
Events from FIRST (2001-01-01).

Decision rule (fixed before the first run), on universe (c), PCT = 8%, 20 sessions:
    Per calendar month of the entry, the mean excess (winsorized at the 1st/99th percentile of the
    events); Newey-West t across months, lag 1 (lag 2 for 30 sessions).
    SUPPORTED    month mean > 0 AND HAC t >= 1.96 AND >= 24 months AND >= 100 events AND the month
                 mean positive in both halves (entries before / from SPLIT, 2014-01-01: halves of
                 about equal length).
    REVERSAL     month mean < 0 AND HAC t <= -1.96 (same minimums).
    INCONCLUSIVE otherwise.
Reported, not decisive: 30 sessions; universe (b); PCT 5% and 10% (two extra looks per universe:
one |t| ~ 2 among them is weak evidence); GDX's own events; the hit rate (share beating SPY) and,
for the options trade that prompted this, the share of events whose raw 30-session return exceeded
+11.7% (what a Dec 18 $85 GDX call bought at $3.20 needs from a $78.95 close).

Not measured: options pricing (implied volatility, time decay), stops, sizing, news. Biases: today's
universe (survivorship), the choice of swing threshold and of "two lower highs/lows" (a definition
that the source doesn't pin down), split adjustment from the cache.

Usage (from a worktree, reading the real caches):
    AETHER_CACHE_DIR=<main>/Data python scripts/backtesting/trend_break_study.py
"""
import argparse
import datetime
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "monitoring"))

import gann_time_cycle_study as gs  # noqa: E402
import position_watch as pw  # noqa: E402
import setup_s10_gate_study as sg  # noqa: E402
from aether import instruments, paths  # noqa: E402

PCT = 0.08
PCT_EXTRA = (0.05, 0.10)
HORIZONS = (20, 30)
LAGS = {20: 1, 30: 2}
COST_BPS = 10.0
FIRST = "2001-01-01"
SPLIT = "2014-01-01"
GATE_T = 1.96
MIN_MONTHS = 24
MIN_EVENTS = 100
OPTION_NEED = 0.117


def find_events(closes: list, pct: float = PCT) -> list:
    """Confirmation indices k of the pattern (see the docstring). Uses only turns confirmed by k."""
    turns = gs.zigzag_indices(closes, pct)
    out = []
    for n, (i0, kind, k) in enumerate(turns):
        if kind != "low":
            continue
        prior = turns[:n]                                  # all confirmed before this low's confirmation
        highs = [closes[i] for i, t, _ in prior if t == "high"]
        lows = [closes[i] for i, t, _ in prior if t == "low"]
        if len(highs) < 4 or len(lows) < 3:
            continue
        L0, L1, L2, L3 = closes[i0], lows[-1], lows[-2], lows[-3]
        H0, H1, H2, H3 = highs[-1], highs[-2], highs[-3], highs[-4]
        if L0 > L1 and H0 > H1 and H1 < H2 < H3 and L1 < L2 < L3:
            out.append(k)
    return out


def excess(b: dict, spy: dict, k: int, h: int) -> float | None:
    """Stock minus SPY (%) from the open of k+1 to the close of k+h, minus costs."""
    s, e = k + 1, k + h
    if e >= len(b["c"]) or b["o"][s] <= 0:
        return None
    d0, d1 = b["days"][s], b["days"][e]
    if d0 not in spy["idx"] or d1 not in spy["idx"]:
        return None
    j0, j1 = spy["idx"][d0], spy["idx"][d1]
    if spy["o"][j0] <= 0:
        return None
    r = b["c"][e] / b["o"][s] - 1
    m = spy["c"][j1] / spy["o"][j0] - 1
    return (r - m) * 100 - COST_BPS / 100


def raw_return(b: dict, k: int, h: int) -> float | None:
    s, e = k + 1, k + h
    if e >= len(b["c"]) or b["o"][s] <= 0:
        return None
    return b["c"][e] / b["o"][s] - 1


def _q(xs, q):
    s = sorted(xs)
    return s[min(len(s) - 1, max(0, int(q * (len(s) - 1))))] if s else None


def monthly_test(events: list, key: str, lag: int, bounds=None) -> dict:
    xs = [e[key] for e in events if e.get(key) is not None]
    if bounds is None:
        bounds = (_q(xs, 0.01), _q(xs, 0.99)) if xs else (0, 0)
    by = {}
    for e in events:
        x = e.get(key)
        if x is not None:
            by.setdefault(e["date"][:7], []).append(max(bounds[0], min(bounds[1], x)))
    months = sorted(by)
    means = [sg._mean(by[m]) for m in months]
    t = sg._t_hac(means, lag) if len(means) > lag + 2 else None
    return {"events": sum(len(v) for v in by.values()), "months": len(months),
            "month_mean": round(sg._mean(means), 3) if means else None,
            "t_hac": round(t, 2) if t is not None else None,
            "beat_spy": round(sum(x > 0 for x in xs) / len(xs), 3) if xs else None, "bounds": bounds}


def verdict(primary: dict, half1: dict, half2: dict) -> str:
    m, t = primary.get("month_mean"), primary.get("t_hac")
    if m is None or t is None or primary["months"] < MIN_MONTHS or primary["events"] < MIN_EVENTS:
        return "INCONCLUSIVE"
    if m > 0 and t >= GATE_T and (half1.get("month_mean") or 0) > 0 and (half2.get("month_mean") or 0) > 0:
        return "SUPPORTED"
    if m < 0 and t <= -GATE_T:
        return "REVERSAL"
    return "INCONCLUSIVE"


def summarize(events: list) -> dict:
    out = {}
    for h in HORIZONS:
        key = f"x{h}"
        p = monthly_test(events, key, LAGS[h])
        h1 = monthly_test([e for e in events if e["date"] < SPLIT], key, LAGS[h], p["bounds"])
        h2 = monthly_test([e for e in events if e["date"] >= SPLIT], key, LAGS[h], p["bounds"])
        out[f"{h}d"] = {"all": p, "before_split": h1, "from_split": h2, "verdict": verdict(p, h1, h2)}
    r30 = [e["r30"] for e in events if e.get("r30") is not None]
    out["share_r30_above_option_need"] = round(sum(r > OPTION_NEED for r in r30) / len(r30), 3) if r30 else None
    return out


def collect(symbols, spy, pct) -> list:
    events = []
    for s in symbols:
        try:
            with open(os.path.join(paths.ohlcv_dir(), f"{s}_daily.json"), encoding="utf-8") as f:
                b = pw.bars_from_series(json.load(f).get("Time Series (Daily)", {}))
        except (OSError, ValueError):
            continue
        if not b:
            continue
        for k in find_events(b["c"], pct):
            if k + 1 >= len(b["c"]) or b["days"][k + 1] < FIRST:
                continue
            events.append({"symbol": s, "date": b["days"][k + 1], "confirm": b["days"][k],
                           "x20": excess(b, spy, k, 20), "x30": excess(b, spy, k, 30),
                           "r20": raw_return(b, k, 20), "r30": raw_return(b, k, 30)})
    return events


def is_etf(sym: str) -> bool:
    snaps = sorted(glob.glob(os.path.join(paths.symbol_dir(), sym, f"{sym}_*.json")))
    for p in reversed(snaps[-5:]):
        try:
            with open(p, encoding="utf-8") as f:
                meta = (json.load(f).get("metaInfo") or [{}])[0]
        except (OSError, ValueError):
            continue
        return bool(str(meta.get("etf_group_name") or "").strip())
    return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--max-symbols", type=int, default=None, help="smoke test on a slice (don't read results)")
    a = ap.parse_args(argv)
    files = glob.glob(os.path.join(paths.ohlcv_dir(), "*_daily.json"))
    symbols = sorted(os.path.basename(f)[:-len("_daily.json")] for f in files)
    symbols = [s for s in symbols if s != "SPY" and not instruments.is_excluded(s)]
    if a.max_symbols:
        symbols = sorted(set(symbols[:a.max_symbols]) | ({"GDX"} & set(symbols)))
    with open(os.path.join(paths.ohlcv_dir(), "SPY_daily.json"), encoding="utf-8") as f:
        spy = pw.bars_from_series(json.load(f)["Time Series (Daily)"])
    etfs = sorted(s for s in symbols if is_etf(s))
    stocks = [s for s in symbols if s not in set(etfs)]
    rep = {"run": datetime.datetime.now().isoformat(timespec="seconds"), "first": FIRST, "split": SPLIT,
           "etfs": etfs, "n_stocks": len(stocks), "smoke_test": bool(a.max_symbols), "by_pct": {}}
    for pct in (PCT, *PCT_EXTRA):
        st, et, gdx = collect(stocks, spy, pct), collect(etfs, spy, pct), collect(["GDX"], spy, pct)
        rep["by_pct"][f"{pct:.2f}"] = {
            "stocks": summarize(st), "etfs": summarize(et), "gdx": summarize(gdx),
            "gdx_events": [{k: (round(v, 4) if isinstance(v, float) else v) for k, v in e.items()} for e in gdx]}
    prim = rep["by_pct"][f"{PCT:.2f}"]["stocks"]["20d"]
    rep["verdict"] = prim["verdict"]
    out = os.path.join(paths.data_dir(), f"trend_break_study_{datetime.datetime.now():%Y-%m-%d_%H%M%S}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1, default=str)
    sys.stdout.write(json.dumps({"verdict": rep["verdict"], "etfs": etfs, "n_stocks": len(stocks),
                                 "by_pct": {p: {u: {h: v[h] for h in ("20d", "30d", "share_r30_above_option_need")}
                                                for u, v in d.items() if u != "gdx_events"}
                                            for p, d in rep["by_pct"].items()},
                                 "gdx_events_8pct": rep["by_pct"][f"{PCT:.2f}"]["gdx_events"]},
                                indent=1, default=str) + f"\nwrote {out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
