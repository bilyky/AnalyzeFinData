"""
Digit-Sum Score Out of Sample (R&D #72): rebuild the live digit-sum index from bars before 2023
only, then judge it on 2023-01..today, which it has never seen.

The live score (aether/scoring.py digit_sum_score) adds a per-symbol "numerology" term in -1..+1
to every Short10, and half of it to Long60, from the prior close's digit sum (integer part and
full cents). Its index (Data/digit_sum_*study.json) keeps every per-symbol bucket with |z| >= 2,
N >= 50 and a temporal check, fitted on the whole history: with ~500 symbols x 9 digits x 2 kinds,
about 1 bucket in 20 passes |z| >= 2 by chance.

Rebuild (as of 2022-12-31): the digit_sum_study / digit_sum_full_study bucket counts (CLOSE: digit
of close_t -> next session up = close > open; OPEN: digit of open_t -> same session) on bars whose
outcome falls before CUTOFF; the temporal check (_study_utils.window_analysis) on the same
truncated bars; then the live gate itself (scoring._build_digit_index) and the live formula
(scoring.digit_sum_score, with the rebuilt index swapped in). Raw (unadjusted) prices, as live:
the digit sum is of the traded price.

Out of sample, 2023-01-01 on (#72's three measures):
  (a) hit rate, reported: for each signal the index fires (prior close's bucket), the next
      session up minus the symbol's in-sample base rate, signed by the score; mean per date,
      Newey-West t lag 1; next to the same measure in sample (how much the index shrinks), and
      out of sample against each symbol's own up rate over the same period (added after the
      first run, when an old base turned the 2023-26 market's up drift into an apparent hit).
  (b) rank IC: per date among today's gated candidates (rule_walkforward CURRENT mask on the
      replay scores, digit off), Spearman IC of S10 + digit vs the 20-day forward return minus
      IC of S10 alone; Newey-West t lag 19 (rank_ic_study's machinery).
  (c) portfolio: portfolio_backtest.simulate with today's rules on the replay panel, scores with
      digit (S10 + d, L60 + d/2, clipped to +/-10, rounded to 0.1 as live) vs without; daily
      return difference over the whole window, Newey-West t lag 5 (R&D #58 method). Context:
      win rate on R&D #62's 50 draws (seed 62).

Decision rule (fixed in the roadmap before this script existed, R&D #72):
  KEEP    (b) mean > 0 with HAC t >= 1.96, OR (c) mean > 0 with HAC t >= 1.96.
  REMOVE  otherwise: take digit_sum out of the live Short10 and Long60 in its own PR (a live
          scoring change, judged under the risk rules).
  #72 also names #67's deflated Sharpe for (c); #67 isn't built, so (c) uses the HAC t alone
  (disclosed). Candidates for (b) are gated on S10 without the digit term, so the IC difference
  is about ordering only.

Not measured: the open-price digit (live it only logs a line), costs beyond 10 bps, taxes.
Biases: today's universe; snapshots after the R&D #74 quarantine.

Usage (from a worktree, reading the real caches):
    AETHER_CACHE_DIR=<main>/Data python scripts/backtesting/digit_sum_holdout_study.py
"""
import argparse
import contextlib
import datetime
import glob
import json
import math
import multiprocessing
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

import _study_utils as su  # noqa: E402
import backtest_ratings as _br  # noqa: E402
import instruments  # noqa: E402
import portfolio_backtest as pb  # noqa: E402
import rank_ic_study as ric  # noqa: E402
import rule_walkforward as wf  # noqa: E402
import setup_s10_gate_study as sg  # noqa: E402
from aether import paths, scoring  # noqa: E402
from aether.config import CFG  # noqa: E402

CUTOFF = "2023-01-01"
MIN_N = 50
MIN_ABS_Z = 2.0
GATE_T = 1.96
FNS = {"int": scoring._price_digit_sum, "full": scoring._price_digit_sum_full}


def _z(ups, n, base):
    if n < MIN_N:
        return 0.0
    se = math.sqrt(base * (1 - base) / n)
    return ((ups / n) - base) / se if se > 0 else 0.0


def _oc(bar):
    try:
        return float(bar["1. open"]), float(bar["4. close"])
    except (KeyError, ValueError, TypeError):
        return None


def bucket_rows(sym, ts, fn, cutoff=CUTOFF) -> tuple:
    """digit_sum_study's per-symbol rows on outcomes before `cutoff`; also the CLOSE base rate."""
    dates = sorted(ts)
    open_b, close_b = {}, {}
    for i, day in enumerate(dates):
        oc = _oc(ts[day])
        if oc is None or day >= cutoff:
            continue
        op, cl = oc
        b = open_b.setdefault(fn(op), [0, 0])
        b[0] += cl > op
        b[1] += 1
        if i + 1 < len(dates) and dates[i + 1] < cutoff and (nx := _oc(ts[dates[i + 1]])) is not None:
            b = close_b.setdefault(fn(cl), [0, 0])
            b[0] += nx[1] > nx[0]
            b[1] += 1
    rows, bases = [], {}
    for typ, bkt in (("OPEN", open_b), ("CLOSE", close_b)):
        tn = sum(v[1] for v in bkt.values())
        base = sum(v[0] for v in bkt.values()) / tn if tn else 0.5
        bases[typ] = base
        for dg in range(10):
            ups, n = bkt.get(dg, [0, 0])
            if n >= MIN_N:
                rows.append({"symbol": sym, "type": typ, "digit": dg, "up_pct": round(ups / n, 4),
                             "base": round(base, 4), "n": n, "z": round(_z(ups, n, base), 3)})
    return rows, bases["CLOSE"]


def symbol_job(job):
    """Index rows (both digit kinds) from pre-cutoff bars, plus every OOS (day, prior close, next up)."""
    sym, ohlcv_dir = job
    try:
        with open(os.path.join(ohlcv_dir, f"{sym}_daily.json"), encoding="utf-8") as f:
            ts = json.load(f).get("Time Series (Daily)", {})
    except (OSError, ValueError):
        return sym, None
    dates = sorted(ts)
    if len(dates) < 100:
        return sym, None
    cut = {d: v for d, v in ts.items() if d < CUTOFF}
    out = {"rows": {}, "base": None, "oos": [], "ins": []}
    for kind, fn in FNS.items():
        rows, base = bucket_rows(sym, ts, fn)
        for r in rows:
            if abs(r["z"]) >= MIN_ABS_Z:
                r.update(su.window_analysis(r["digit"], r["z"], cut, fn, r["type"]))
        out["rows"][kind] = rows
        out["base"] = base if kind == "int" else out["base"]
    for i in range(1, len(dates) - 1):
        prev, nx = _oc(ts[dates[i - 1]]), _oc(ts[dates[i + 1]])
        cur = _oc(ts[dates[i]])
        if prev is None or cur is None or nx is None or prev[1] <= 0:
            continue
        # CLOSE signal on day i: digit of close_i -> session i+1. The replay's digit for day i
        # uses close_{i-1}; both are kept: "sig" for (a), "prior" for (b)/(c).
        rec = (dates[i], cur[1], int(nx[1] > nx[0]), prev[1])
        (out["oos"] if dates[i + 1] >= CUTOFF else out["ins"]).append(rec)
    return sym, out


@contextlib.contextmanager
def live_index(rows_int, rows_full):
    """The live gate (scoring._build_digit_index) on rebuilt rows, swapped into scoring."""
    with tempfile.TemporaryDirectory() as tmp:
        p_int, p_full = os.path.join(tmp, "int.json"), os.path.join(tmp, "full.json")
        for p, rows in ((p_int, rows_int), (p_full, rows_full)):
            with open(p, "w", encoding="utf-8") as f:
                json.dump(rows, f)
        idx, idx_full = scoring._build_digit_index(p_int), scoring._build_digit_index(p_full)
    saved = scoring._digit_index, scoring._digit_full_index
    scoring._digit_index, scoring._digit_full_index = idx, idx_full
    try:
        yield idx, idx_full
    finally:
        scoring._digit_index, scoring._digit_full_index = saved


def hit_test(per_sym: dict, key: str, same_period: bool = False) -> dict:
    """(a): per date, mean of sign(score) * (next up - base) over fired signals. base = the symbol's
    in-sample (pre-cutoff) up rate, or with same_period its up rate over the same records: in a
    rising market an old base makes mostly-bullish signals look right (market drift, not digits)."""
    by_day, bullish = {}, 0
    for sym, data in per_sym.items():
        recs = data[key]
        if same_period and not recs:
            continue
        base = sum(u for _d, _c, u, _p in recs) / len(recs) if same_period else data["base"]
        for day, close, up, _prev in recs:
            s = scoring.digit_sum_score(sym, close)
            if s:
                bullish += s > 0
                by_day.setdefault(day, []).append((1 if s > 0 else -1) * (up - base))
    days = sorted(by_day)
    means = [sum(by_day[d]) / len(by_day[d]) for d in days]
    t = sg._t_hac(means, 1)
    n = sum(len(v) for v in by_day.values())
    return {"signals": n, "days": len(days), "bullish_share": round(bullish / n, 3) if n else None,
            "base": "same period" if same_period else "pre-cutoff",
            "excess_hit_pct": round(100 * float(np.mean(means)), 3) if means else None,
            "t_hac": round(t, 2) if t is not None else None}


def digit_by_day(per_sym: dict) -> dict:
    """{sym: {day: digit score from the prior close}} for OOS days (the replay's convention)."""
    return {sym: {day: scoring.digit_sum_score(sym, prev) for day, _c, _u, prev in data["oos"]}
            for sym, data in per_sym.items()}


def with_digit(s10, l60, d):
    return round(max(-10.0, min(10.0, s10 + d)), 1), round(max(-10.0, min(10.0, l60 + d * 0.5)), 1)


def ic_test(rows, dmap) -> dict:
    """(b): IC(S10 + digit) - IC(S10) among today's gated candidates, 20-day returns, lag 19."""
    a = wf.to_arrays(rows)
    d = np.array([dmap.get(str(s), {}).get(str(day), 0.0) for day, s in zip(a["day"], a["sym"])], dtype=float)
    s10d = np.array([with_digit(x, 0.0, y)[0] for x, y in zip(a["s10"].astype(float), d)])
    mask = wf.admit_mask(a, wf.CURRENT) & (a["day"] >= CUTOFF)
    ic_base = ric.daily_ic(a["day"], a["s10"].astype(float), a["f20"].astype(float), mask)
    ic_dig = ric.daily_ic(a["day"], s10d, a["f20"].astype(float), mask)
    return {"candidates_with_digit": int((mask & (d != 0)).sum()), "candidates": int(mask.sum()),
            "s10": ric.summarize_ic(ic_base, 19), "s10_plus_digit": ric.summarize_ic(ic_dig, 19),
            "difference": ric.diff(ic_dig, ic_base, 19)}


def digit_panel(panel: dict, dmap: dict) -> dict:
    return {s: {day: list(with_digit(v[0], v[1], dmap.get(s, {}).get(day, 0.0))) for day, v in by.items()}
            for s, by in panel.items()}


def daily_returns(curve):
    eq = [e for _d, e, _i in curve]
    return {curve[k][0]: eq[k] / eq[k - 1] - 1 for k in range(1, len(eq)) if eq[k - 1] > 0}


def return_diff_test(curve_a, curve_b, lag=5) -> dict:
    ra, rb = daily_returns(curve_a), daily_returns(curve_b)
    days = sorted(set(ra) & set(rb))
    diffs = [ra[d] - rb[d] for d in days]
    t = sg._t_hac(diffs, lag)
    return {"days": len(days), "mean_bps_per_day": round(1e4 * float(np.mean(diffs)), 3) if diffs else None,
            "t_hac": round(t, 2) if t is not None else None}


def decide(ic_diff: dict, port: dict) -> str:
    def pos(x):                               # a HAC t carries its mean's sign: t >= 1.96 means mean > 0
        return x.get("t_hac") is not None and x["t_hac"] >= GATE_T
    return "KEEP" if pos(ic_diff) or pos(port) else "REMOVE"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--draws", type=int, default=50)
    ap.add_argument("--wf-panel", default=os.path.join(tempfile.gettempdir(), "rnd72_rule_wf_panel.pkl"))
    ap.add_argument("--max-symbols", type=int, default=None, help="smoke test on a slice (don't read results)")
    ap.add_argument("--hit-only", action="store_true", help="measure (a) only (both bases); skip (b) and (c)")
    a = ap.parse_args(argv)
    ohlcv_dir, cache = paths.ohlcv_dir(), paths.cache_dir()
    symbols = sorted(os.path.basename(f)[:-len("_daily.json")] for f in glob.glob(os.path.join(ohlcv_dir, "*_daily.json")))
    if a.max_symbols:
        symbols = sorted(set(symbols[:a.max_symbols]) | {"SPY"})
    with multiprocessing.Pool(a.workers) as pool:
        per_sym = {s: d for s, d in pool.imap_unordered(symbol_job, [(s, ohlcv_dir) for s in symbols], chunksize=4) if d}
    rows_int = [r for d in per_sym.values() for r in d["rows"]["int"]]
    rows_full = [r for d in per_sym.values() for r in d["rows"]["full"]]
    rep = {"run": datetime.datetime.now().isoformat(timespec="seconds"), "cutoff": CUTOFF, "symbols": len(per_sym),
           "smoke_test": bool(a.max_symbols)}
    with live_index(rows_int, rows_full) as (idx, idx_full):
        rep["index"] = {"int_kept": len(idx), "full_kept": len(idx_full),
                        "close_kept": sum(k[1] == "CLOSE" for k in idx) + sum(k[1] == "CLOSE" for k in idx_full),
                        "buckets_tested": len(rows_int) + len(rows_full)}
        rep["a_hit_in_sample"] = hit_test(per_sym, "ins")
        rep["a_hit_out_of_sample"] = hit_test(per_sym, "oos")
        rep["a_hit_out_of_sample_same_period_base"] = hit_test(per_sym, "oos", same_period=True)
        dmap = digit_by_day(per_sym)
    if a.hit_only:
        out = os.path.join(paths.data_dir(), f"digit_sum_holdout_hit_{datetime.datetime.now():%Y-%m-%d_%H%M%S}.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=1)
        sys.stdout.write(json.dumps(rep, indent=1) + f"\nwrote {out}\n")
        return 0
    if a.max_symbols:                        # a slice: never touch the full caches
        sym_dir = os.path.join(cache, "Symbol")
        wf_rows = [r for s in symbols if os.path.isdir(os.path.join(sym_dir, s))
                   for r in wf._symbol_rows((s, sym_dir, ohlcv_dir))]
        pb.PANEL_PATH = os.path.join(tempfile.mkdtemp(), "panel.json")
    else:
        wf_rows = wf.build_panel(cache, a.wf_panel)
    rep["b_rank_ic"] = ic_test(wf_rows, dmap)
    syms_pb = [s for s in symbols if os.path.isdir(os.path.join(_br.SYM_DIR, s))]
    panel = pb.build_panel(syms_pb, 2023, a.workers)
    bars = {s: b for s in set(syms_pb) | {"SPY"} if (ts := _br.load_ohlcv(s)) and (b := pb.bars_from_series(ts))}
    calendar = [d for d in sorted(bars["SPY"]["idx"]) if d >= max(CUTOFF, min(min(v) for v in panel.values() if v))]
    excluded = frozenset(s for s in syms_pb if instruments.is_excluded(s))
    eligible = [s for s in syms_pb if s not in excluded]
    dpanel = digit_panel(panel, dmap)
    kw = {"risk_rules": True, "cost_bps": 10.0, "excluded": excluded, "max_trade_risk_pct": CFG.system_max_trade_risk_pct}
    base_run, dig_run = pb.simulate(panel, bars, calendar, **kw), pb.simulate(dpanel, bars, calendar, **kw)
    rep["c_portfolio"] = {"without": pb.metrics(base_run), "with_digit": pb.metrics(dig_run),
                          "daily_diff": return_diff_test(dig_run["curve"], base_run["curve"])}

    def table(syms, days):
        sub, _ = pb.path_universe(panel, eligible, syms)
        dsub, _ = pb.path_universe(dpanel, eligible, syms)
        return {"aether_rules": pb.metrics(pb.simulate(sub, bars, days, **kw)),
                "aether_digit": pb.metrics(pb.simulate(dsub, bars, days, **kw))}
    summ = pb.run_robustness(pb.draw_paths(syms_pb, calendar, a.draws, split="2025-01-01"), calendar,
                             "2025-01-01", table)
    rep["c_draws_win_rate"] = {p: summ["periods"][p]["aether_digit"]["win_rate"] for p in ("full", "train", "test")}
    rep["verdict"] = decide(rep["b_rank_ic"]["difference"], rep["c_portfolio"]["daily_diff"])
    out = os.path.join(paths.data_dir(), f"digit_sum_holdout_study_{datetime.datetime.now():%Y-%m-%d_%H%M%S}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1)
    sys.stdout.write(json.dumps(rep, indent=1) + f"\nwrote {out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
