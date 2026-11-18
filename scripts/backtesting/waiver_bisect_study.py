"""
Waiver Bisect Study (R&D #112) — did letting the R&D #34 waiver fire on Setup make the picks worse?

Question. `aether/scoring.short_score` waives the contrarian penalties (industry Strong -2.0 -> 0,
LT trend Strong -1.5 -> 0) and flips positive momentum from -0.30 to +0.15 when the breakout waiver
fires. Commit f05e685 (2026-08-24) changed the key it reads from 'setup' (never populated, so only a
Bull regime fired it) to 'setup_ok'. Before 08-24 the live game ran "Bull only"; since then "Bull or
setup_ok". Live ledger: 29 buys 06-17..08-23 (median +1.38 pp vs SPY over 10 sessions, 15/29 beat
SPY) vs 4 buys 08-24..08-30 (1 beat, mean -4.64 pp). Too few to decide, hence this replay.

Variants (short_score's `waiver` argument; the scoring code is the production function):
    V2 = "bull_or_setup"  (current live rule)
    V1 = "bull_only"      (live before 2026-08-24)
    V0 = "none"           (never waive)

Universe and dates. Every symbol with both a Chaikin snapshot folder (Data/Symbol) and an OHLCV file
(Data/Symbol_full), snapshot days from 2023-01-01 that are OHLCV sessions, with the filters of
backtest_ratings.process_symbol (valid status, snapshot price > 0 and within 10% of the OHLCV close,
at least 20 prior sessions). Fields come from backtest_ratings.compute_br (the production fields
dict); the study captures that dict and scores it once per variant with scoring.short_score.
Digit-sum stays off (compute_br's study default: its index is in-sample).

Picks. For each day, candidates = rows with setup_ok True (the game only buys active-setup rows) and
that variant's short score (S10) >= 2.0. Picks = the top 6 by short score,
ties broken by symbol (ascending). Picks are chosen before outcomes are looked at; a pick with no
outcome (missing forward bars) is dropped from the means and counted.
Correction after the run (2026-11-18, review of PR #291; wording only, no code change): the 2.0 cut
here is on S10 alone. The live game gates on S10+L60 >= min_score_threshold (steps.py total_score)
with its own adaptive S10 floor, so this replay asks "does the waiver change make an S10-ranked top 6
worse?", which is close to but not the live selector. A live-like selector would be a new,
separately pre-registered study.

Outcome (lesson #85: never feature and outcome from the same price). From split-adjusted, real
(non-placeholder) bars via position_watch.bars_from_series: t+1 = the first bar after the snapshot
day, outcome = close[t+11] / close[t+1] - 1 minus SPY's return over the same two dates, in pp. Days
run to the latest snapshot day with 10 forward sessions.

Primary statistic. Per day, mean outcome of V1 picks minus mean outcome of V2 picks, on days where
both have >= 1 pick with an outcome. Newey-West t with lag 9 (daily samples of 10-session outcomes;
setup_s10_gate_study._t_hac). Halves: days before / from 2025-01-01.

Decision rule (fixed before the full run):
    REVERT_TO_BULL_ONLY  mean > 0 and HAC t >= 1.96 and both half means > 0
    KEEP_SETUP_WAIVER    mean < 0 and HAC t <= -1.96 and both half means < 0
    otherwise            INCONCLUSIVE (keep the live rule; no evidence to change it)
Secondary, same rule, labelled REVERT_TO_NO_WAIVER / KEEP_SETUP_WAIVER / INCONCLUSIVE: V0 minus V2.
Also reported: each variant's picks vs SPY (pooled mean, median, share beating SPY, and the per-day
mean with its HAC t), the share of days the variants' pick sets differ, the outcome distribution
(percentiles, extremes), and a descriptive slice 2026-08-01..2026-10-31 (no verdict).

Caveats (to disclose with any result). ~20% of Data/Symbol snapshots are wrong-date backfills (R&D
#74), and 2026-08-31..11-04 snapshots have empty Chaikin fields; both hit all variants alike.
Survivorship: today's universe. This is a pick-level replay, not the full game: no stops, sizing,
Rule B, pyramids, slots or cash. Nothing here is wired.

Usage:
    AETHER_CACHE_DIR=<main>/Data python scripts/backtesting/waiver_bisect_study.py [--workers N]
        [--max-symbols N] [--out PATH]
"""
import argparse
import bisect
import datetime
import glob
import json
import multiprocessing
import os
import statistics
import sys
from collections import defaultdict
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "monitoring"))
import backtest_ratings as br
import position_watch as pw
import setup_s10_gate_study as sg

from aether import paths
from aether.scoring import short_score

VARIANTS = {"V2": "bull_or_setup", "V1": "bull_only", "V0": "none"}
MIN_SCORE = 2.0
TOP_N = 6
HORIZON = 10
HAC_LAG = HORIZON - 1
GATE_T = 1.96
MIN_YEAR = "2023-01-01"
HALF_SPLIT = "2025-01-01"
SLICE = ("2026-08-01", "2026-10-31")
MIN_IDX = max(br.SMA_DAYS, br.TARGET_LOOKBACK, 3)


def _out(line: str = "") -> None:
    sys.stdout.write(line + "\n")


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def _r(x, nd=3):
    return round(x, nd) if x is not None else None


# ── data ─────────────────────────────────────────────────────────────────────

def capture_fields(data, prev, price, idx, dates, ts, seas):
    """compute_br's result and the production `fields` dict it handed to short_score.

    compute_br builds the dict and scores it in place; the study needs the dict itself to score it
    under each waiver mode, so the module's score hook is swapped for a recorder for this one call."""
    seen = []
    real = br._short_score_fn

    def recorder(fields):
        if not seen:
            seen.append(dict(fields))
        return real(fields)

    br._short_score_fn = recorder
    try:
        res = br.compute_br(data, prev, price, idx, dates, ts, seas)
    finally:
        br._short_score_fn = real
    return res, seen[0]


def forward_window(adj: dict, day: str, horizon: int = HORIZON):
    """(t+1 date, t+1+horizon date, return) from split-adjusted bars, starting the bar AFTER `day`."""
    days = adj["days"]
    j1 = bisect.bisect_right(days, day)
    j2 = j1 + horizon
    if j2 >= len(days):
        return None
    c1, c2 = adj["c"][j1], adj["c"][j2]
    if c1 <= 0 or c2 <= 0:
        return None
    return days[j1], days[j2], c2 / c1 - 1


def _symbol_rows(job):
    """Candidate rows (setup_ok True) for one symbol: day, symbol, score per variant, forward window."""
    sym, sym_dir, ohlcv_dir = job
    ts = sg._load(ohlcv_dir, sym)
    if not ts:
        return []
    dates = sorted(ts)
    pos = {d: i for i, d in enumerate(dates)}
    seas = br.precompute_seasonality(ts)
    adj = pw.bars_from_series(ts)
    rows, prev = [], None
    for path in sorted(glob.glob(os.path.join(sym_dir, sym, f"{sym}_*.json"))):
        day = Path(path).stem[len(sym) + 1:]
        if day < MIN_YEAR or day not in pos:
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
        idx = pos[day]
        close = float(ts[day].get("4. close", 0) or 0)
        if price <= 0 or idx < MIN_IDX or (close > 0 and abs(price - close) / close > 0.1):
            prev = data
            continue
        res, fields = capture_fields(data, prev, price, idx, dates, ts, seas)
        prev = data
        if fields.get("setup_ok") is not True:
            continue
        scores = {v: short_score(fields, waiver=m) for v, m in VARIANTS.items()}
        fw = forward_window(adj, day) if adj else None
        rows.append({"day": day, "sym": sym, "s": scores, "parity": scores["V2"] == res[1],
                     "d1": fw[0] if fw else None, "d2": fw[1] if fw else None, "ret": fw[2] if fw else None})
    return rows


def collect(sym_dir, ohlcv_dir, symbols, workers):
    jobs = [(s, sym_dir, ohlcv_dir) for s in symbols]
    if workers <= 1:
        parts = map(_symbol_rows, jobs)
        return [r for part in parts for r in part]
    rows = []
    with multiprocessing.Pool(workers) as pool:
        for n, part in enumerate(pool.imap_unordered(_symbol_rows, jobs, chunksize=4), 1):
            rows.extend(part)
            if n % 100 == 0 or n == len(jobs):
                sys.stderr.write(f"  {n}/{len(jobs)} symbols, {len(rows)} candidate rows\n")
    return rows


def excess_pp(row, spy_adj):
    """Pick return minus SPY over the same two dates, in pp (None if either side is missing)."""
    if row["ret"] is None or not spy_adj:
        return None
    i1, i2 = spy_adj["idx"].get(row["d1"]), spy_adj["idx"].get(row["d2"])
    if i1 is None or i2 is None:
        return None
    s1, s2 = spy_adj["c"][i1], spy_adj["c"][i2]
    if s1 <= 0:
        return None
    return (row["ret"] - (s2 / s1 - 1)) * 100


# ── selection, pairing, verdict ──────────────────────────────────────────────

def select_picks(rows, variant, min_score=MIN_SCORE, top_n=TOP_N):
    """day -> picks for one variant: setup_ok rows (already filtered) with score >= min_score,
    top `top_n` by score, ties by symbol."""
    by_day = defaultdict(list)
    for r in rows:
        if r["s"][variant] >= min_score:
            by_day[r["day"]].append(r)
    return {d: sorted(rs, key=lambda r: (-r["s"][variant], r["sym"]))[:top_n] for d, rs in by_day.items()}


def day_means(picks):
    """day -> mean excess of the picks that have an outcome (days with none are left out)."""
    out = {}
    for d, rs in picks.items():
        xs = [r["x"] for r in rs if r.get("x") is not None]
        if xs:
            out[d] = _mean(xs)
    return out


def paired_diff(means_a, means_b):
    """Sorted (day, mean_a - mean_b) over days where both variants have an outcome."""
    return [(d, means_a[d] - means_b[d]) for d in sorted(set(means_a) & set(means_b))]


def verdict(mean, t, half_means, pos_label, neg_label="KEEP_SETUP_WAIVER", gate=GATE_T):
    if mean is None or t is None or any(h is None for h in half_means):
        return "INCONCLUSIVE"
    if mean > 0 and t >= gate and all(h > 0 for h in half_means):
        return pos_label
    if mean < 0 and t <= -gate and all(h < 0 for h in half_means):
        return neg_label
    return "INCONCLUSIVE"


def compare(diffs, pos_label, split=HALF_SPLIT):
    xs = [x for _, x in diffs]
    first = [x for d, x in diffs if d < split]
    second = [x for d, x in diffs if d >= split]
    m, t = _mean(xs), sg._t_hac(xs, HAC_LAG)
    halves = [_mean(first), _mean(second)]
    return {"days": len(xs), "mean_pp": _r(m), "t_hac": _r(t, 2), "t_plain": _r(sg._t_one_sample(xs), 2),
            "half1_days": len(first), "half1_mean_pp": _r(halves[0]), "half1_t_hac": _r(sg._t_hac(first, HAC_LAG), 2),
            "half2_days": len(second), "half2_mean_pp": _r(halves[1]), "half2_t_hac": _r(sg._t_hac(second, HAC_LAG), 2),
            "days_nonzero": sum(1 for x in xs if x != 0),
            "verdict": verdict(m, t, halves, pos_label)}


def variant_stats(picks):
    xs = [r["x"] for rs in picks.values() for r in rs if r.get("x") is not None]
    n_all = sum(len(rs) for rs in picks.values())
    dm = day_means(picks)
    series = [dm[d] for d in sorted(dm)]
    out = {"days_with_picks": len(picks), "picks": n_all, "picks_with_outcome": len(xs),
           "picks_without_outcome": n_all - len(xs), "days_with_outcome": len(series)}
    if xs:
        q = statistics.quantiles(xs, n=100)
        out.update({"mean_pp": _r(_mean(xs)), "median_pp": _r(statistics.median(xs)),
                    "share_beat_spy": _r(sum(1 for x in xs if x > 0) / len(xs)),
                    "p1_pp": _r(q[0]), "p99_pp": _r(q[-1]), "min_pp": _r(min(xs)), "max_pp": _r(max(xs)),
                    "day_mean_pp": _r(_mean(series)), "day_mean_t_hac": _r(sg._t_hac(series, HAC_LAG), 2)})
    return out


def share_different(picks_a, picks_b):
    days = sorted(set(picks_a) | set(picks_b))
    diff = sum(1 for d in days
               if {r["sym"] for r in picks_a.get(d, [])} != {r["sym"] for r in picks_b.get(d, [])})
    return {"days": len(days), "days_different": diff, "share": _r(diff / len(days)) if days else None}


def summarize(rows, last_day=None, lo=None, hi=None):
    """Full report block over rows with lo <= day <= hi (and day <= last_day)."""
    keep = [r for r in rows if (lo is None or r["day"] >= lo) and (hi is None or r["day"] <= hi)
            and (last_day is None or r["day"] <= last_day)]
    picks = {v: select_picks(keep, v) for v in VARIANTS}
    means = {v: day_means(p) for v, p in picks.items()}
    return {
        "candidate_rows": len(keep),
        "variants": {v: variant_stats(p) for v, p in picks.items()},
        "V1_minus_V2": compare(paired_diff(means["V1"], means["V2"]), "REVERT_TO_BULL_ONLY"),
        "V0_minus_V2": compare(paired_diff(means["V0"], means["V2"]), "REVERT_TO_NO_WAIVER"),
        "share_days_different": {"V1_vs_V2": share_different(picks["V1"], picks["V2"]),
                                 "V0_vs_V2": share_different(picks["V0"], picks["V2"])},
    }


def last_full_day(spy_adj):
    """Latest day d with 10 forward SPY sessions after the first bar following d."""
    days = spy_adj["days"]
    return days[len(days) - HORIZON - 2] if len(days) > HORIZON + 2 else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument("--max-symbols", type=int, default=None)
    ap.add_argument("--symbols", default=None, help="comma list (smoke tests)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    sym_dir, ohlcv_dir = paths.symbol_dir(), paths.ohlcv_dir()
    symbols = sorted({Path(p).name.replace("_daily.json", "") for p in glob.glob(os.path.join(ohlcv_dir, "*_daily.json"))}
                     & {d for d in os.listdir(sym_dir) if os.path.isdir(os.path.join(sym_dir, d))})
    if args.symbols:
        symbols = [s for s in args.symbols.split(",") if s in symbols]
    if args.max_symbols:
        symbols = symbols[:args.max_symbols]
    spy_adj = pw.bars_from_series(sg._load(ohlcv_dir, "SPY") or {})
    rows = collect(sym_dir, ohlcv_dir, symbols, args.workers)
    for r in rows:
        r["x"] = excess_pp(r, spy_adj)
    last_day = last_full_day(spy_adj)
    report = {
        "meta": {"run_at": datetime.datetime.now().isoformat(timespec="seconds"), "symbols": len(symbols),
                 "min_day": MIN_YEAR, "last_day": last_day, "first_candidate_day": min((r["day"] for r in rows), default=None),
                 "variants": VARIANTS, "min_score": MIN_SCORE, "top_n": TOP_N, "horizon": HORIZON, "hac_lag": HAC_LAG,
                 "half_split": HALF_SPLIT, "v2_parity_mismatches": sum(1 for r in rows if not r["parity"]),
                 "rows_beyond_last_day": sum(1 for r in rows if last_day and r["day"] > last_day)},
        "all": summarize(rows, last_day),
        "slice_2026_08_10": summarize(rows, last_day, *SLICE),
    }
    _print(report)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        _out(f"\nreport -> {args.out}")


def _print(report):
    m = report["meta"]
    _out(f"Waiver bisect study (R&D #112) — {m['symbols']} symbols, {m['min_day']}..{m['last_day']}, "
         f"V2 parity mismatches {m['v2_parity_mismatches']}")
    for name in ("all", "slice_2026_08_10"):
        b = report[name]
        _out(f"\n[{name}] candidate rows {b['candidate_rows']}")
        for v, s in b["variants"].items():
            _out(f"  {v} ({VARIANTS[v]}): days {s['days_with_picks']} picks {s['picks']} (no outcome {s['picks_without_outcome']})"
                 f"  mean {s.get('mean_pp')} pp  median {s.get('median_pp')}  beat SPY {s.get('share_beat_spy')}"
                 f"  day-mean {s.get('day_mean_pp')} (HAC t {s.get('day_mean_t_hac')})  p1/p99 {s.get('p1_pp')}/{s.get('p99_pp')}")
        for k in ("V1_minus_V2", "V0_minus_V2"):
            c = b[k]
            _out(f"  {k}: days {c['days']} (nonzero {c['days_nonzero']}) mean {c['mean_pp']} pp  HAC t {c['t_hac']}"
                 f"  plain t {c['t_plain']}  halves {c['half1_mean_pp']} ({c['half1_days']}d) / {c['half2_mean_pp']}"
                 f" ({c['half2_days']}d)  -> {c['verdict']}")
        _out(f"  pick sets differ: {b['share_days_different']}")


if __name__ == "__main__":
    main()
