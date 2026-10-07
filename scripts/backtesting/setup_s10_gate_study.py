"""
Setup x S10 Entry-Gate Study — is requiring BOTH gates too strict?

PROD 2026-10-01: the AI game admits a buy candidate only if Setup = 1 (price > SMA20
AND price > close[3d ago]) AND Short10 >= 2.0 (adaptive momentum floor) AND the
combined score clears the profile minimum. In a pullback week only ~1 name/day passes,
while ~79 names with S10 >= 2 and combined >= 5 are excluded solely by Setup = 0.
Each gate was introduced on its own evidence; the conjunction was never measured.

Method — same historical reconstruction as backtest_ratings.py (Chaikin cache fields
-> current short_score/long_score; Setup via the shared compute_setup_ok), 10- and
20-day forward returns from OHLCV. Universe filter: combined (S10+L60) >= 5.0, the
BALANCED minimum. Groups:

    BOTH        Setup = 1 and S10 >= 2.0   (today's gate)
    S10_ONLY    Setup = 0 and S10 >= 2.0   (excluded by Setup alone)
    SETUP_ONLY  Setup = 1 and S10 <  2.0   (excluded by the floor alone)
    BASE        every qualifying observation

Primary test (pre-registered): S10_ONLY minus BOTH on fwd10, DATE-CLUSTERED — the
cross-sectional mean difference on each day where both groups are present, then a
t-test across days, so one market move is not counted hundreds of times. Because
neighbouring days' 10-day windows overlap, the verdict uses a Newey-West (HAC, lag 9)
t; the plain t and the every-10th-day (non-overlapping) t are reported alongside. Repeated on PULLBACK days (SPY below
its 20-day SMA with a negative 10-day return).

Decision rule (stated before running):
    TOO STRICT  S10_ONLY is not worse than BOTH (HAC t > -1.96) AND
                S10_ONLY beats BASE (HAC t >= 1.96)  -> consider relaxing
    History (disclosed): the rule first pre-registered decided on the plain overlapping
    t, which gave TOO_STRICT on 2023+. The statistic was changed AFTER that run — first to
    the non-overlapping median t (INCONCLUSIVE), then to HAC on review. HAC is the rule
    fixed for every future re-run.
    KEEP        S10_ONLY is significantly worse than BOTH (HAC t <= -1.96)
    otherwise   INCONCLUSIVE — keep the gate (no evidence to loosen)

Usage:
    python scripts/backtesting/setup_s10_gate_study.py [--min-year 2023] [--data-dir PATH]
"""
import argparse
import datetime
import glob
import json
import math
import multiprocessing
import os
import sys
from collections import defaultdict
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))
import backtest_ratings as br

S10_FLOOR = 2.0
MIN_COMBINED = 5.0
GATE_T = 1.96
FWD = (10, 20)


def _out(line: str = "") -> None:
    sys.stdout.write(line + "\n")


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def _t_one_sample(xs):
    """t of mean(xs) vs 0."""
    n = len(xs)
    if n < 3:
        return None
    m = _mean(xs)
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    return m / math.sqrt(var / n) if var > 0 else None


def _t_hac(xs, lag):
    """Newey-West (HAC, Bartlett kernel) t of mean(xs) vs 0 — the standard estimator when
    consecutive observations share overlapping forward windows (lag = horizon - 1)."""
    n = len(xs)
    if n < lag + 3:
        return None
    m = _mean(xs)
    d = [x - m for x in xs]
    lrv = sum(v * v for v in d) / n
    for k in range(1, lag + 1):
        gamma = sum(d[i] * d[i - k] for i in range(k, n)) / n
        lrv += 2 * (1 - k / (lag + 1)) * gamma
    return m / math.sqrt(lrv / n) if lrv > 0 else None


def _spy_pullback_days(ohlcv_dir: str) -> set:
    ts = _load(ohlcv_dir, "SPY") or {}
    dates = sorted(ts)
    closes = [float(ts[d]["4. close"]) for d in dates]
    out = set()
    for i in range(20, len(dates)):
        sma20 = sum(closes[i - 20:i]) / 20
        if closes[i] < sma20 and closes[i] < closes[i - 10]:
            out.add(dates[i])
    return out


def _load(ohlcv_dir: str, sym: str):
    path = os.path.join(ohlcv_dir, f"{sym}_daily.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("Time Series (Daily)") or None


def observations(sym_dir: str, ohlcv_dir: str, min_year: int, workers: int = 1):
    """(date, setup_ok, s10, combined, fwd10, fwd20) for every (symbol, date), mirroring
    backtest_ratings.process_symbol's filters (valid status, price sanity vs OHLCV).
    Symbols are independent, so they are fanned out across `workers` processes."""
    syms = sorted({Path(p).name.replace("_daily.json", "") for p in glob.glob(os.path.join(ohlcv_dir, "*_daily.json"))}
                  & {d for d in os.listdir(sym_dir) if os.path.isdir(os.path.join(sym_dir, d))})
    jobs = [(sym, sym_dir, ohlcv_dir, min_year) for sym in syms]
    out = []
    with multiprocessing.Pool(max(1, workers)) as pool:
        for n, rows in enumerate(pool.imap_unordered(_symbol_obs, jobs, chunksize=4), 1):
            out.extend(rows)
            if n % 50 == 0 or n == len(jobs):
                sys.stderr.write(f"  {n}/{len(jobs)} symbols, {len(out)} obs\n")
    return out


def _symbol_obs(job) -> list:
    sym, sym_dir, ohlcv_dir, min_year = job
    rows = []
    ts = _load(ohlcv_dir, sym)
    if ts:
        all_dates = sorted(ts)
        pos = {d: i for i, d in enumerate(all_dates)}
        seasonality = br.precompute_seasonality(ts)
        prev = None
        for path in sorted(glob.glob(os.path.join(sym_dir, sym, f"{sym}_*.json"))):
            day = Path(path).stem[len(sym) + 1:]
            if day < str(min_year) or day not in pos:
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
            ohlcv_close = float(ts[day].get("4. close", 0))
            if (price <= 0 or idx < 20 or idx + max(FWD) >= len(all_dates)
                    or (ohlcv_close > 0 and abs(price - ohlcv_close) / ohlcv_close > 0.1)):
                prev = data
                continue
            res = br.compute_br(data, prev, price, idx, all_dates, ts, seasonality)
            s10, l60 = res[1], res[2]
            fwd = [(float(ts[all_dates[idx + w]]["4. close"]) - price) / price * 100 for w in FWD]
            rows.append((day, br.compute_setup_ok(price, idx, all_dates, ts), s10, s10 + l60, fwd[0], fwd[1]))
            prev = data
    return rows


def _group(setup_ok: bool, s10: float) -> str:
    if s10 >= S10_FLOOR:
        return "BOTH" if setup_ok else "S10_ONLY"
    return "SETUP_ONLY" if setup_ok else "NEITHER"


def _clustered(per_day: dict, a: str, b: str) -> dict:
    """Date-clustered difference: per-day cross-sectional mean(a) - mean(b), t over days.

    Neighbouring days' 10-day forward windows overlap, so the daily diffs are serially
    correlated and the plain `t` overstates significance. `t_hac` (Newey-West, lag 9) is
    the decision statistic; `t_nonoverlap_min` / `_median` repeat the plain test on every
    10th day (each of the 10 offsets) as a cross-check."""
    days = sorted(d for d, g in per_day.items() if g.get(a) and g.get(b))
    diffs = [_mean(per_day[d][a]) - _mean(per_day[d][b]) for d in days]
    t = _t_one_sample(diffs)
    step = FWD[0]
    t_hac = _t_hac(diffs, step - 1)
    ts = sorted(x for x in (_t_one_sample(diffs[k::step]) for k in range(step)) if x is not None)
    return {"days": len(diffs), "mean_diff10": round(_mean(diffs), 3) if diffs else None,
            "t": round(t, 2) if t is not None else None,
            "t_hac": round(t_hac, 2) if t_hac is not None else None,
            "t_nonoverlap_min": round(ts[0], 2) if ts else None,
            "t_nonoverlap_median": round(ts[len(ts) // 2], 2) if ts else None}


def summarize(obs, pullback_days: set) -> dict:
    by_regime = {"ALL": lambda d: True, "PULLBACK": lambda d: d in pullback_days}
    report = {}
    for regime, keep in by_regime.items():
        groups = defaultdict(lambda: {"f10": [], "f20": []})
        per_day = defaultdict(lambda: defaultdict(list))   # day -> group -> [fwd10]
        for day, setup_ok, s10, combined, f10, f20 in obs:
            if combined < MIN_COMBINED or not keep(day):
                continue
            g = _group(setup_ok, s10)
            for name in (g, "BASE"):
                groups[name]["f10"].append(f10)
                groups[name]["f20"].append(f20)
                per_day[day][name].append(f10)
        stats = {}
        for name, v in groups.items():
            f10 = v["f10"]
            stats[name] = {"n": len(f10), "days": sum(1 for d in per_day.values() if d.get(name)),
                           "mean10": round(_mean(f10), 3), "win10": round(sum(x > 0 for x in f10) / len(f10), 3),
                           "mean20": round(_mean(v["f20"]), 3)}

        tests = {"S10_ONLY_vs_BOTH": _clustered(per_day, "S10_ONLY", "BOTH"),
                 "S10_ONLY_vs_BASE": _clustered(per_day, "S10_ONLY", "BASE"),
                 "BOTH_vs_BASE": _clustered(per_day, "BOTH", "BASE")}
        # Decision statistic: Newey-West HAC t (lag 9). The plain t is reported but
        # overstates significance on overlapping windows.
        t_vs_both = tests["S10_ONLY_vs_BOTH"]["t_hac"]
        t_vs_base = tests["S10_ONLY_vs_BASE"]["t_hac"]
        if t_vs_both is not None and t_vs_both <= -GATE_T:
            verdict = "KEEP"
        elif t_vs_both is not None and t_vs_base is not None and t_vs_both > -GATE_T and t_vs_base >= GATE_T:
            verdict = "TOO_STRICT"
        else:
            verdict = "INCONCLUSIVE"
        report[regime] = {"groups": stats, "tests": tests, "verdict": verdict}
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-year", type=int, default=2023)
    ap.add_argument("--data-dir", default=os.path.join(os.path.dirname(os.path.dirname(_HERE)), "Data"))
    ap.add_argument("--out", default=None, help="write the JSON report here (default: stdout only)")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    args = ap.parse_args()
    sym_dir = os.path.join(args.data_dir, "Symbol")
    ohlcv_dir = os.path.join(args.data_dir, "Symbol_full")

    obs = observations(sym_dir, ohlcv_dir, args.min_year, args.workers)
    report = summarize(obs, _spy_pullback_days(ohlcv_dir))
    report["meta"] = {"min_year": args.min_year, "observations": len(obs), "s10_floor": S10_FLOOR,
                      "min_combined": MIN_COMBINED, "run_date": str(datetime.date.today()),
                      "last_obs_date": max((o[0] for o in obs), default=None)}

    _out(f"Setup x S10 gate study — {len(obs)} obs since {args.min_year} "
         f"(last {report['meta']['last_obs_date']}), combined >= {MIN_COMBINED}")
    for regime in ("ALL", "PULLBACK"):
        r = report[regime]
        _out(f"\n[{regime}]  verdict: {r['verdict']}")
        for name in ("BOTH", "S10_ONLY", "SETUP_ONLY", "NEITHER", "BASE"):
            g = r["groups"].get(name)
            if g:
                _out(f"  {name:10s} n={g['n']:>7} days={g['days']:>4}  fwd10 {g['mean10']:+.2f}%  "
                     f"win10 {g['win10']:.1%}  fwd20 {g['mean20']:+.2f}%")
        for k, v in r["tests"].items():
            _out(f"  {k:18s} days={v['days']:>4}  mean diff fwd10 {v['mean_diff10']}  t={v['t']}  "
                 f"HAC t={v['t_hac']}  non-overlap t median={v['t_nonoverlap_median']} min={v['t_nonoverlap_min']}")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        _out(f"\nreport -> {args.out}")


if __name__ == "__main__":
    main()
