"""
Gann time cycles (R&D #87): does the market repeat its past on the cycles W. D. Gann's novel names
that our data can reach?

Source: W. D. Gann, "The Tunnel Thru the Air" (1927; full text, Internet Archive item
tunnelthruairorl00gann). The Foreword says it holds "a valuable secret, clothed in veiled
language". Its hero's stated method (chapter VII, "Future Cycles"): "I get the past history and
find out what cycle we are in and then predict the curve for the future, which is a repetition of
past market movements." The cycles it names: four minutes, the day, the year ("the four seasons"),
and 100, 500, 1000, 2000 and 5000 years. Our daily data (1999+) reaches only the year; the long
cycles and the astrology the book also leans on can't be tested here.

Tests (the year cycle, three readings):
  H1a  one-year repetition, per stock: a stock's return over the 20 sessions after a month-end
       vs its return over the same 20-session window one year earlier (the trading day on or
       before the same date a year back). Per month-end, the Spearman IC across stocks
       (>= MIN_NAMES); mean IC over month-ends; Newey-West t, lag 1.
  H1b  the same with the average of the same window over the prior 5 years (all 5 needed): the
       steadier form of "repetition"; close to classic per-stock seasonality and to the app's live
       seasonality slot factor (backtest_ratings.precompute_seasonality).
  H2   seasonal turning points, SPY: major turns (10% zigzag on closes) within +/- WINDOW_DAYS
       calendar days of Mar 21 / Jun 21 / Sep 23 / Dec 21. The book names "the four seasons", not
       these dates; the dates are the followers' reading. p = exact one-sided binomial vs a
       uniform day of year (chance 4 x 15 / 365). (A circular-shift null was dropped before the
       first run: the dates are ~91 days apart, so quarter shifts map them onto each other and
       even turns exactly on the dates could not get p below ~0.12.)

Split for H1: month-ends before SPLIT (2014-01-01) find, from SPLIT on confirm. H2 has too few
turns to split (a few dozen in 27 years), so it can only be weak evidence.

Decision rule (fixed before the first run):
  1. p-values: H1a, H1b two-sided from the TRAIN HAC t (normal); H2 the exact binomial p.
     Benjamini-Hochberg at Q (0.10) across the three.
  2. H1x CONFIRMED: discovered AND the test mean IC has the same sign with |HAC t| >= 1.96.
     H2 SUPPORTED_WEAK: discovered (no holdout possible).
  3. Verdict: GANN_TIMING_SUPPORTED if any H1x is CONFIRMED; else NOT_SUPPORTED (a lone H2
     pass is reported, not acted on). Even a pass is a reason for a portfolio study, never a
     scoring change from here.

Not measured: costs, portfolio use, the long cycles, astrology, the novel's intraday cycle.
Biases: today's universe back to 1999 (survivorship: dead stocks missing), split-adjusted closes
from the cache, ETFs/leveraged/crypto excluded (instruments.is_excluded) except SPY for H2.

Usage (from a worktree, reading the real caches):
    AETHER_CACHE_DIR=<main>/Data python scripts/backtesting/gann_time_cycle_study.py
"""
import bisect
import datetime
import glob
import json
import math
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "monitoring"))

import position_watch as pw  # noqa: E402
import setup_s10_gate_study as sg  # noqa: E402
from aether import instruments, paths  # noqa: E402

HORIZON = 20
YEARS_AVG = 5
MIN_NAMES = 30
LAG = 1
SPLIT = "2014-01-01"
Q = 0.10
GATE_T = 1.96
ZIGZAG = 0.10
WINDOW_DAYS = 7
SEASON_DATES = ((3, 21), (6, 21), (9, 23), (12, 21))
FIRST = "2001-01-01"


def month_ends(days: list) -> list:
    """Trading days whose next trading day falls in another month."""
    return [d for d, nxt in zip(days, days[1:]) if d[:7] != nxt[:7]]


def _on_or_before(days: list, date: str):
    k = bisect.bisect_right(days, date) - 1
    return k if k >= 0 else None


def fwd(b: dict, i: int, h: int = HORIZON):
    """Return over the h sessions after bar i, or None."""
    if i is None or i + h >= len(b["c"]) or b["c"][i] <= 0:
        return None
    return b["c"][i + h] / b["c"][i] - 1


def years_back(day: str, k: int) -> str:
    d = datetime.date.fromisoformat(day)
    try:
        return d.replace(year=d.year - k).isoformat()
    except ValueError:                                   # Feb 29
        return d.replace(year=d.year - k, day=28).isoformat()


def past_windows(b: dict, day: str, i: int, years: int) -> list:
    """The same 20-session window 1..years years before `day` (each must end before bar i)."""
    out = []
    for k in range(1, years + 1):
        j = _on_or_before(b["days"], years_back(day, k))
        r = fwd(b, j) if j is not None and j + HORIZON <= i else None
        if r is None:
            return out
        out.append(r)
    return out


def symbol_rows(sym: str, b: dict, sample_days: list) -> list:
    rows = []
    for day in sample_days:
        i = b["idx"].get(day)
        f = fwd(b, i)
        if f is None:
            continue
        past = past_windows(b, day, i, YEARS_AVG)
        if not past:
            continue
        rows.append({"day": day, "sym": sym, "f20": f, "lag1": past[0],
                     "avg5": sum(past) / YEARS_AVG if len(past) == YEARS_AVG else None})
    return rows


def ic_series(df: pd.DataFrame, col: str) -> pd.Series:
    """Per day: Spearman IC of `col` vs f20 among names with both (days with >= MIN_NAMES)."""
    d = df[["day", col, "f20"]].dropna()
    out = {}
    for day, g in d.groupby("day"):
        if len(g) >= MIN_NAMES:
            rx, ry = g[col].rank(), g["f20"].rank()
            if rx.std() > 0 and ry.std() > 0:
                out[day] = float(rx.corr(ry))
    return pd.Series(out, dtype=float).sort_index()


def summarize(ics) -> dict:
    xs = list(ics)
    t = sg._t_hac(xs, LAG) if len(xs) > LAG + 2 else None
    return {"n": len(xs), "mean_ic": round(sum(xs) / len(xs), 4) if xs else None,
            "t_hac": round(t, 2) if t is not None else None}


def p_two_sided(t):
    return math.erfc(abs(t) / math.sqrt(2)) if t is not None else None


def zigzag_turns(days: list, closes: list, pct: float = ZIGZAG) -> list:
    """[(day, 'high'|'low')] confirmed turns: an extreme followed by a move of `pct` against it."""
    turns, direction, hi, lo, ext = [], 0, 0, 0, 0   # direction: +1 tracking a high, -1 a low, 0 not yet
    for k in range(1, len(closes)):
        c = closes[k]
        if direction == 0:
            hi = k if c > closes[hi] else hi
            lo = k if c < closes[lo] else lo
            if c <= closes[hi] * (1 - pct):
                turns.append((days[hi], "high"))
                direction, ext = -1, k
            elif c >= closes[lo] * (1 + pct):
                turns.append((days[lo], "low"))
                direction, ext = 1, k
        elif direction == 1:
            if c > closes[ext]:
                ext = k
            elif c <= closes[ext] * (1 - pct):
                turns.append((days[ext], "high"))
                direction, ext = -1, k
        else:
            if c < closes[ext]:
                ext = k
            elif c >= closes[ext] * (1 + pct):
                turns.append((days[ext], "low"))
                direction, ext = 1, k
    return turns


def _doy(day: str) -> int:
    d = datetime.date.fromisoformat(day)
    return datetime.date(2001, d.month, min(d.day, 28) if d.month == 2 else d.day).timetuple().tm_yday


def near_count(turn_days: list, targets_doy: list, window: int = WINDOW_DAYS) -> int:
    """Turns within `window` days (circular day-of-year, 365-day year) of any target."""
    n = 0
    for td in turn_days:
        x = _doy(td)
        if any(min(abs(x - t), 365 - abs(x - t)) <= window for t in targets_doy):
            n += 1
    return n


def binom_tail(k: int, n: int, p: float) -> float:
    """P(X >= k) for X ~ Binomial(n, p)."""
    return min(1.0, sum(math.comb(n, j) * p ** j * (1 - p) ** (n - j) for j in range(k, n + 1)))


def season_test(turn_days: list) -> dict:
    """Turns near the four season dates vs a uniform day of year: exact one-sided binomial p."""
    base = [datetime.date(2001, m, d).timetuple().tm_yday for m, d in SEASON_DATES]
    obs, n = near_count(turn_days, base), len(turn_days)
    p0 = len(base) * (2 * WINDOW_DAYS + 1) / 365
    return {"turns": n, "near": obs, "share": round(obs / n, 3) if n else None, "chance_share": round(p0, 3),
            "p_binom": round(binom_tail(obs, n, p0), 5) if n else None}


def benjamini_hochberg(pvals: dict, q: float = Q) -> set:
    items = sorted(((p, k) for k, p in pvals.items() if p is not None))
    m, cut = len(items), 0
    for rank, (p, _k) in enumerate(items, 1):
        if p <= q * rank / m:
            cut = rank
    return {k for _p, k in items[:cut]}


def evaluate(h1: dict, h2: dict) -> dict:
    """h1: {"H1a"|"H1b": {"train": summary, "test": summary}}; h2: season_test output."""
    pv = {k: p_two_sided(v["train"]["t_hac"]) for k, v in h1.items()}
    pv["H2"] = h2.get("p_binom")
    disc = benjamini_hochberg(pv)
    out = {"p": {k: (round(p, 5) if p is not None else None) for k, p in pv.items()}, "discovered": sorted(disc)}
    for k, v in h1.items():
        tr, te = v["train"], v["test"]
        ok = (k in disc and te["t_hac"] is not None and abs(te["t_hac"]) >= GATE_T
              and tr["mean_ic"] is not None and (te["t_hac"] > 0) == (tr["mean_ic"] > 0))
        out[k] = "CONFIRMED" if ok else "NOT_CONFIRMED"
    out["H2"] = "SUPPORTED_WEAK" if "H2" in disc else "NOT_SUPPORTED"
    out["verdict"] = "GANN_TIMING_SUPPORTED" if any(out[k] == "CONFIRMED" for k in h1) else "NOT_SUPPORTED"
    return out


def _bars(sym):
    try:
        with open(os.path.join(paths.ohlcv_dir(), f"{sym}_daily.json"), encoding="utf-8") as f:
            b = pw.bars_from_series(json.load(f).get("Time Series (Daily)", {}))
    except (OSError, ValueError):
        return None
    return b


def main() -> int:
    files = glob.glob(os.path.join(paths.ohlcv_dir(), "*_daily.json"))
    symbols = sorted(os.path.basename(f)[:-len("_daily.json")] for f in files)
    spy = _bars("SPY")
    sample = [d for d in month_ends(spy["days"]) if d >= FIRST]
    rows = []
    for s in symbols:
        if s == "SPY" or instruments.is_excluded(s):
            continue
        b = _bars(s)
        if b:
            rows += symbol_rows(s, b, sample)
    df = pd.DataFrame(rows)
    h1 = {}
    for name, col in (("H1a", "lag1"), ("H1b", "avg5")):
        ic = ic_series(df, col)
        h1[name] = {"train": summarize(ic[ic.index < SPLIT]), "test": summarize(ic[ic.index >= SPLIT]),
                    "all": summarize(ic)}
    turns = zigzag_turns(spy["days"], spy["c"])
    h2 = season_test([d for d, _k in turns])
    ev = evaluate({k: {"train": v["train"], "test": v["test"]} for k, v in h1.items()}, h2)
    rep = {"run": datetime.datetime.now().isoformat(timespec="seconds"), "rows": len(df),
           "symbols": int(df.sym.nunique()) if len(df) else 0, "first": df.day.min() if len(df) else None,
           "last": df.day.max() if len(df) else None, "split": SPLIT, "H1": h1, "H2": h2,
           "spy_turns": turns, "evaluation": ev,
           "source": "W. D. Gann, The Tunnel Thru the Air (1927), Internet Archive tunnelthruairorl00gann"}
    out = os.path.join(paths.data_dir(), f"gann_time_cycle_study_{datetime.datetime.now():%Y-%m-%d_%H%M%S}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1)
    show = {k: v for k, v in rep.items() if k != "spy_turns"}
    sys.stdout.write(json.dumps(show, indent=1) + f"\nwrote {out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
