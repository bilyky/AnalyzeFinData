"""
stretch_regime_study.py — R&D #89: after a deep short-term stretch down, does price snap back, does
that depend on the era or on the market regime, and can we tell beforehand whether a lower low
comes first?

Raised 2026-11-17. Tech was stretched (XLK 7.8% under its 20-day average, RSI-14 29). A quick check
pooled 2000-2026: after similar stretches XLK did about as well as on any day over 20 sessions, and
a lower low came within 20 sessions in 52 of 59 cases. Split by period, 2000-09 kept falling while
2010-22 and 2023+ bounced. The owner asked: do the history checks need a pre-AI / AI-era split, and
are there patterns that tell whether a lower low is coming? The quick check is the hypothesis
source; this study re-tests it with rules fixed here, before the full run.

Event (per symbol, only data up to that close):
    RSI-14 (simple averages of gains and losses, as the quick check) <= 30, AND
    d20 = close / mean(prior 20 closes) - 1 at or below the 5th percentile of the symbol's own d20
    over the prior 756 sessions (at least 504 needed).
    Events are at least 20 sessions apart per symbol (no overlapping outcome windows per symbol).
Outcomes start at the NEXT close (no shared price with the features; #85's lesson):
    r20   = close[t+21] / close[t+1] - 1
    ex20  = r20 - the symbol's mean r20 over all days of the same era (pp): the snap-back over what
            that symbol normally did in that era. Primary for H1/H2.
    spy20 = r20 - SPY's return over the same dates (pp); reported only.
    LL    = min(low[t+1..t+20]) < low[t]  (a lower low within 20 sessions). Primary for H3.
    MAE   = min(low[t+1..t+20]) / close[t] - 1; reported only.
Universes: symbols with OHLCV and a Chaikin snapshot folder (the screen + its ETFs). ETF = the latest
snapshot's metaInfo.etf_group_name is non-blank (the #88 rule; the copy here goes once #275 merges).
Eras by event date: 2000-2009, 2010-2022, 2023+. Regime: SPY close vs its prior 200-close mean.

Statistics: events are grouped by calendar month (events in one month share the market move); the
monthly means are tested with a Newey-West t, lag 1 (20-session outcomes overlap into the next
month). ex20 is winsorized at the pooled 1st/99th percentiles.

Decision rules (fixed before the run; per universe, ETFs and stocks separately):
  H1 snap-back by era:
    SNAPBACK_RECENT  2010-2022 ex20 month mean > 0 with t >= 1.96 (>= 24 months, >= 100 events)
                     AND the 2023+ month mean > 0 (2023+ is too short for its own t; sign only).
    NO_SNAPBACK      2010-2022 t <= -1.96.
    INCONCLUSIVE     otherwise.
  H2 era or regime (cells: early = 2000-2009, late = 2010+; SPY above / below its 200-day mean):
    d_above = late - early within SPY-above, d_below = the same within SPY-below; d_regime =
    above - below, all eras pooled. Welch t on the cells' monthly means. A cell needs >= 12 months.
    ERA_EFFECT       d_above t >= 1.96 AND d_below t >= 1.96.
    REGIME_NOT_ERA   |t(d_above)| < 1.96 AND |t(d_below)| < 1.96 AND |t(d_regime)| >= 1.96.
    INSUFFICIENT     a cell has < 12 months.
    INCONCLUSIVE     otherwise.
  H3 lower-low predictors. Features at the event close: close_loc ((close-low)/(high-low)), vol_ratio
    (volume / prior 50-day mean), gap (open / prior close - 1), dd252 (close / 252-day high close -
    1), r5 (5-session return), above200 (symbol above its 200-day mean), spy_above200, rsi14,
    down_streak (consecutive lower closes), d20. Train = events before 2019-01-01, test = after.
    Statistic: LL rate in the top tercile minus the bottom tercile (cut-offs from train; binary
    features: 1 minus 0), p from a month-block bootstrap (2,000 resamples, seed 89).
    Benjamini-Hochberg at q = 0.10 over the 20 train tests (10 features x 2 universes).
    CONFIRMED        a train discovery whose test difference has the same sign with one-sided
                     bootstrap p < 0.05.
    Verdict per universe: PREDICTORS_CONFIRMED (list) or NONE_CONFIRMED.
Any verdict only proposes; nothing is wired from this study (INCONCLUSIVE keeps today's checks).

Not measured: costs, stops, position sizing, options, news, intraday paths. Biases: today's
universe (survivorship), today's ETF labels, a cache split-adjusted by bars_from_series. The 2023+
era is short; its sign is context, not a test. The SPY-above/below cells can share a month when
the regime flips mid-month (a small dependence the Welch t ignores).

Usage (from a worktree, reading the real caches):
    AETHER_CACHE_DIR=<main>/Data python scripts/backtesting/stretch_regime_study.py
    ... --max-symbols 40   (smoke test on a slice; don't read results)
"""
import argparse
import datetime
import glob
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "monitoring"))

import feature_ic_subgroup_study as fic  # noqa: E402
import position_watch as pw  # noqa: E402
import setup_s10_gate_study as sg  # noqa: E402
from aether import paths  # noqa: E402

RSI_MAX = 30.0
D20_PCTL = 0.05
HIST, MIN_HIST = 756, 504
GAP = 20
H = 20
LAG = 1
ERAS = (("2000-2009", "2000-01-01", "2010-01-01"), ("2010-2022", "2010-01-01", "2023-01-01"),
        ("2023+", "2023-01-01", "9999-12-31"))
TRAIN_END = "2019-01-01"
GATE_T = 1.96
MIN_MONTHS, MIN_EVENTS, MIN_CELL_MONTHS = 24, 100, 12
Q = 0.10
BOOT, SEED = 2000, 89
FEATURES = ("close_loc", "vol_ratio", "gap", "dd252", "r5", "above200", "spy_above200", "rsi14",
            "down_streak", "d20")
BINARY = {"above200", "spy_above200"}
CONTEXT = ("XLK", "SPY", "RSP", "INTC", "GDX")


def era_of(day: str) -> str | None:
    for name, a, z in ERAS:
        if a <= day < z:
            return name
    return None


def rsi_simple(c: np.ndarray, k: int = 14) -> np.ndarray:
    """RSI from simple k-day averages of gains and losses; NaN until k changes exist."""
    out = np.full(len(c), np.nan)
    if len(c) <= k:
        return out
    d = np.diff(c)
    g, lo = np.clip(d, 0, None), np.clip(-d, 0, None)
    cg, cl = np.concatenate(([0.0], np.cumsum(g))), np.concatenate(([0.0], np.cumsum(lo)))
    for j in range(k, len(c)):
        ag, al = (cg[j] - cg[j - k]) / k, (cl[j] - cl[j - k]) / k
        out[j] = 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)
    return out


def prior_mean(c: np.ndarray, k: int) -> np.ndarray:
    """mean(c[j-k:j]) at j (the k closes before j); NaN until k exist."""
    out = np.full(len(c), np.nan)
    cs = np.concatenate(([0.0], np.cumsum(c)))
    for j in range(k, len(c)):
        out[j] = (cs[j] - cs[j - k]) / k
    return out


def find_events(c: np.ndarray) -> list:
    """Event indices: RSI-14 <= 30 and d20 at/below its own trailing 5th percentile; >= GAP apart."""
    rsi, sma20 = rsi_simple(c), prior_mean(c, 20)
    d20 = c / sma20 - 1
    ev, last = [], -10 ** 9
    for j in range(len(c)):
        if not (rsi[j] <= RSI_MAX) or j - last < GAP:
            continue
        hist = d20[max(0, j - HIST):j]
        hist = hist[~np.isnan(hist)]
        if len(hist) < MIN_HIST or not d20[j] <= np.quantile(hist, D20_PCTL):
            continue
        ev.append(j)
        last = j
    return ev


def down_streak(c: np.ndarray, j: int) -> int:
    n = 0
    while j - n - 1 >= 0 and c[j - n] < c[j - n - 1]:
        n += 1
    return n


def arrays(b: dict) -> tuple:
    return tuple(np.asarray(b[k], float) for k in ("c", "h", "l", "o", "v"))


def features(b: dict, j: int, spy_up: dict, arr: tuple | None = None) -> dict:
    """Features at close j (data up to j only). arr: arrays(b), passed to avoid rebuilding."""
    c, h, lo, o, v = arr or arrays(b)
    rng = h[j] - lo[j]
    vm = float(np.mean(v[j - 50:j])) if j >= 50 else 0.0
    s200 = float(np.mean(c[j - 200:j])) if j >= 200 else float("nan")
    sma20 = float(np.mean(c[j - 20:j]))
    return {"close_loc": (c[j] - lo[j]) / rng if rng > 0 else None,
            "vol_ratio": v[j] / vm if vm > 0 and v[j] > 0 else None,
            "gap": o[j] / c[j - 1] - 1 if c[j - 1] > 0 and o[j] > 0 else None,
            "dd252": c[j] / float(np.max(c[max(0, j - 252):j + 1])) - 1,
            "r5": c[j] / c[j - 5] - 1,
            "above200": (1.0 if c[j] > s200 else 0.0) if not math.isnan(s200) else None,
            "spy_above200": spy_up.get(b["days"][j]),
            "rsi14": float(rsi_simple(c[j - 15:j + 1])[-1]),
            "down_streak": float(down_streak(c, j)),
            "d20": c[j] / sma20 - 1}


def outcomes(b: dict, j: int, base: dict, spy: dict) -> dict | None:
    c, lo = b["c"], b["l"]
    if j + 1 + H >= len(c) or c[j + 1] <= 0:
        return None
    r20 = c[j + 1 + H] / c[j + 1] - 1
    era = era_of(b["days"][j])
    out = {"r20": r20 * 100, "ex20": (r20 - base[era]) * 100 if base.get(era) is not None else None}
    d0, d1 = b["days"][j + 1], b["days"][j + 1 + H]
    if d0 in spy["idx"] and d1 in spy["idx"]:
        out["spy20"] = (r20 - (spy["c"][spy["idx"][d1]] / spy["c"][spy["idx"][d0]] - 1)) * 100
    window = lo[j + 1:j + 1 + H]
    out["ll"] = 1.0 if min(window) < lo[j] else 0.0
    out["mae"] = (min(window) / c[j] - 1) * 100
    return out


def era_base(b: dict) -> dict:
    """The symbol's mean r20 (from the next close) per era, over all its days."""
    c, days, acc = b["c"], b["days"], {}
    for j in range(len(c) - 1 - H):
        if c[j + 1] > 0:
            acc.setdefault(era_of(days[j]), []).append(c[j + 1 + H] / c[j + 1] - 1)
    return {k: sum(v) / len(v) for k, v in acc.items() if k and v}


def spy_regime(spy: dict) -> dict:
    c = np.asarray(spy["c"], float)
    s200 = prior_mean(c, 200)
    return {d: (1.0 if c[j] > s200[j] else 0.0) for j, d in enumerate(spy["days"]) if not math.isnan(s200[j])}


def symbol_events(sym: str, b: dict, spy: dict, spy_up: dict) -> list:
    base, out = era_base(b), []
    arr = arrays(b)
    c = arr[0]
    for j in find_events(c):
        if j < 252:
            continue
        o = outcomes(b, j, base, spy)
        if o is None or era_of(b["days"][j]) is None:
            continue
        out.append({"symbol": sym, "date": b["days"][j], "era": era_of(b["days"][j]),
                    **features(b, j, spy_up, arr), **o})
    return out


def monthly(events: list, key: str, bounds=None) -> dict:
    """Month means of `key` (winsorized) and their Newey-West t (lag 1)."""
    xs = [e[key] for e in events if e.get(key) is not None]
    if bounds is None:
        bounds = (float(np.quantile(xs, 0.01)), float(np.quantile(xs, 0.99))) if xs else (0.0, 0.0)
    by = {}
    for e in events:
        if e.get(key) is not None:
            by.setdefault(e["date"][:7], []).append(max(bounds[0], min(bounds[1], e[key])))
    months = sorted(by)
    means = [sum(by[m]) / len(by[m]) for m in months]
    t = sg._t_hac(means, LAG) if len(means) > LAG + 2 else None
    return {"events": len(xs), "months": len(months), "means": means,
            "month_mean": round(float(np.mean(means)), 3) if means else None,
            "t_hac": round(t, 2) if t is not None else None, "bounds": bounds}


def welch_t(a: list, b: list) -> float | None:
    if len(a) < 2 or len(b) < 2:
        return None
    va, vb = float(np.var(a, ddof=1)), float(np.var(b, ddof=1))
    se = math.sqrt(va / len(a) + vb / len(b))
    return (float(np.mean(a)) - float(np.mean(b))) / se if se > 0 else None


def h1_verdict(cells: dict) -> str:
    mid, late = cells.get("2010-2022") or {}, cells.get("2023+") or {}
    t, m = mid.get("t_hac"), mid.get("month_mean")
    if t is None or m is None or mid.get("months", 0) < MIN_MONTHS or mid.get("events", 0) < MIN_EVENTS:
        return "INCONCLUSIVE"
    if m > 0 and t >= GATE_T and (late.get("month_mean") or 0) > 0:
        return "SNAPBACK_RECENT"
    if t <= -GATE_T:
        return "NO_SNAPBACK"
    return "INCONCLUSIVE"


def h2_cells(events: list, bounds) -> dict:
    """Monthly ex20 means per cell: era (early = 2000-2009, late = 2010+) x SPY regime."""
    groups = {"early_above": [], "late_above": [], "early_below": [], "late_below": [], "above": [], "below": []}
    for e in events:
        reg = {1.0: "above", 0.0: "below"}.get(e.get("spy_above200"))
        if reg is None:
            continue
        groups[reg].append(e)
        groups[("early_" if e["era"] == "2000-2009" else "late_") + reg].append(e)
    return {k: monthly(v, "ex20", bounds)["means"] for k, v in groups.items()}


def h2_verdict(cells: dict) -> dict:
    if min(len(cells[k]) for k in ("early_above", "late_above", "early_below", "late_below")) < MIN_CELL_MONTHS:
        return {"verdict": "INSUFFICIENT", "months": {k: len(v) for k, v in cells.items()}}
    ta = welch_t(cells["late_above"], cells["early_above"])
    tb = welch_t(cells["late_below"], cells["early_below"])
    tr = welch_t(cells["above"], cells["below"])
    if ta is not None and tb is not None and ta >= GATE_T and tb >= GATE_T:
        v = "ERA_EFFECT"
    elif ta is not None and tb is not None and tr is not None and abs(ta) < GATE_T and abs(tb) < GATE_T \
            and abs(tr) >= GATE_T:
        v = "REGIME_NOT_ERA"
    else:
        v = "INCONCLUSIVE"
    return {"verdict": v, "t_late_minus_early_above": _r(ta), "t_late_minus_early_below": _r(tb),
            "t_above_minus_below": _r(tr), "months": {k: len(x) for k, x in cells.items()},
            "means": {k: (round(float(np.mean(x)), 3) if x else None) for k, x in cells.items()}}


def _r(x):
    return round(x, 2) if x is not None else None


def tercile_cuts(events: list, feat: str):
    xs = [e[feat] for e in events if e.get(feat) is not None]
    if feat in BINARY or len(xs) < 30:
        return None
    return float(np.quantile(xs, 1 / 3)), float(np.quantile(xs, 2 / 3))


def side(e: dict, feat: str, cuts) -> str | None:
    x = e.get(feat)
    if x is None:
        return None
    if feat in BINARY:
        return "top" if x == 1.0 else "bottom"
    return "top" if x >= cuts[1] else ("bottom" if x <= cuts[0] else None)


def ll_diff_boot(events: list, feat: str, cuts, rng: np.random.Generator) -> dict:
    """LL rate top minus bottom, and a month-block bootstrap of it."""
    by = {}
    for e in events:
        s = side(e, feat, cuts)
        if s is None:
            continue
        row = by.setdefault(e["date"][:7], [0.0, 0.0, 0.0, 0.0])   # top n, top LL, bottom n, bottom LL
        k = 0 if s == "top" else 2
        row[k] += 1
        row[k + 1] += e["ll"]
    if not by:
        return {"diff": None, "p_two": None, "p_pos": None, "p_neg": None, "n_top": 0, "n_bottom": 0}
    m = np.array(list(by.values()))
    tot = m.sum(axis=0)
    if tot[0] == 0 or tot[2] == 0:
        return {"diff": None, "p_two": None, "p_pos": None, "p_neg": None, "n_top": int(tot[0]), "n_bottom": int(tot[2])}
    diff = tot[1] / tot[0] - tot[3] / tot[2]
    idx = rng.integers(0, len(m), size=(BOOT, len(m)))
    s = m[idx].sum(axis=1)
    ok = (s[:, 0] > 0) & (s[:, 2] > 0)
    d = s[ok, 1] / s[ok, 0] - s[ok, 3] / s[ok, 2]
    p_le, p_ge = float(np.mean(d <= 0)), float(np.mean(d >= 0))
    return {"diff": round(float(diff), 4), "p_two": min(1.0, 2 * min(p_le, p_ge)), "p_pos": p_le, "p_neg": p_ge,
            "n_top": int(tot[0]), "n_bottom": int(tot[2]), "ll_top": round(float(tot[1] / tot[0]), 3),
            "ll_bottom": round(float(tot[3] / tot[2]), 3)}


def h3_evaluate(results: dict) -> dict:
    """results: {(universe, feat): {"train": stats, "test": stats}} -> discoveries, confirmed, verdicts."""
    disc = fic.benjamini_hochberg({k: v["train"]["p_two"] for k, v in results.items()}, Q)
    confirmed = []
    for k in sorted(disc):
        tr, te = results[k]["train"], results[k]["test"]
        if te["diff"] is None or tr["diff"] is None or te["diff"] == 0:
            continue
        same = (te["diff"] > 0) == (tr["diff"] > 0)
        p_one = te["p_pos"] if tr["diff"] > 0 else te["p_neg"]
        if same and p_one is not None and p_one < 0.05:
            confirmed.append(k)
    verdicts = {}
    for u in sorted({k[0] for k in results}):
        found = [f for uu, f in confirmed if uu == u]
        verdicts[u] = {"verdict": "PREDICTORS_CONFIRMED" if found else "NONE_CONFIRMED", "features": found}
    return {"discoveries": sorted("/".join(k) for k in disc), "confirmed": ["/".join(k) for k in confirmed],
            "verdicts": verdicts}


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


def load_bars(sym: str) -> dict | None:
    try:
        with open(os.path.join(paths.ohlcv_dir(), f"{sym}_daily.json"), encoding="utf-8") as f:
            return pw.bars_from_series(json.load(f).get("Time Series (Daily)", {}))
    except (OSError, ValueError):
        return None


def current_state(sym: str, spy_up: dict) -> dict | None:
    b = load_bars(sym)
    if not b or len(b["c"]) < 260:
        return None
    c = np.asarray(b["c"], float)
    j = len(c) - 1
    ev = find_events(c)
    f = features(b, j, spy_up)
    return {"date": b["days"][j], "event_today": bool(ev and ev[-1] == j),
            "last_event": b["days"][ev[-1]] if ev else None,
            **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in f.items()}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="R&D #89 stretch / snap-back by era and regime")
    ap.add_argument("--max-symbols", type=int, default=None, help="smoke test on a slice (don't read results)")
    a = ap.parse_args(argv)
    spy = load_bars("SPY")
    if not spy:
        sys.stderr.write("no SPY bars\n")
        return 1
    spy_up = spy_regime(spy)
    syms = sorted(os.path.basename(p)[:-len("_daily.json")] for p in glob.glob(os.path.join(paths.ohlcv_dir(), "*_daily.json")))
    syms = [s for s in syms if os.path.isdir(os.path.join(paths.symbol_dir(), s))]
    if a.max_symbols:
        syms = syms[:a.max_symbols]
    events = {"etfs": [], "stocks": []}
    etfs = []
    for s in syms:
        b = load_bars(s)
        if not b or len(b["c"]) < MIN_HIST + 260:
            continue
        u = "etfs" if is_etf(s) else "stocks"
        if u == "etfs":
            etfs.append(s)
        events[u].extend(symbol_events(s, b, spy, spy_up))
    rep = {"run": datetime.datetime.now().isoformat(timespec="seconds"), "smoke_test": bool(a.max_symbols),
           "symbols": len(syms), "etfs": etfs, "spy_last": spy["days"][-1], "h1": {}, "h2": {}, "h3": {},
           "secondary": {}}
    rng = np.random.default_rng(SEED)
    h3 = {}
    for u, ev in events.items():
        pooled = monthly(ev, "ex20")
        cells = {name: monthly([e for e in ev if e["era"] == name], "ex20", pooled["bounds"]) for name, _a, _z in ERAS}
        rep["h1"][u] = {"verdict": h1_verdict(cells), "pooled": {k: v for k, v in pooled.items() if k != "means"},
                        **{k: {kk: vv for kk, vv in v.items() if kk != "means"} for k, v in cells.items()}}
        rep["h2"][u] = h2_verdict(h2_cells(ev, pooled["bounds"]))
        tr = [e for e in ev if e["date"] < TRAIN_END]
        te = [e for e in ev if e["date"] >= TRAIN_END]
        for f in FEATURES:
            cuts = tercile_cuts(tr, f)
            if f not in BINARY and cuts is None:
                continue
            h3[(u, f)] = {"train": ll_diff_boot(tr, f, cuts, rng), "test": ll_diff_boot(te, f, cuts, rng),
                          "cuts": cuts}
        rep["secondary"][u] = {
            "ll_rate": round(float(np.mean([e["ll"] for e in ev])), 3) if ev else None,
            "mae_median": round(float(np.median([e["mae"] for e in ev])), 2) if ev else None,
            "spy20": {k: v for k, v in monthly(ev, "spy20").items() if k != "means"},
            "by_era_ll": {name: round(float(np.mean([e["ll"] for e in ev if e["era"] == name])), 3)
                          for name, _a, _z in ERAS if any(e["era"] == name for e in ev)}}
    rep["h3"] = {"tests": {"/".join(k): v for k, v in h3.items()}, **h3_evaluate(h3)}
    rep["current"] = {s: current_state(s, spy_up) for s in CONTEXT}
    os.makedirs(paths.data_dir(), exist_ok=True)
    out = os.path.join(paths.data_dir(), f"stretch_regime_study_{datetime.datetime.now():%Y-%m-%d_%H%M%S}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1, default=str)
    sys.stdout.write(json.dumps({"out": out, "h1": {u: v["verdict"] for u, v in rep["h1"].items()},
                                 "h2": {u: v["verdict"] for u, v in rep["h2"].items()},
                                 "h3": rep["h3"]["verdicts"]}, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
