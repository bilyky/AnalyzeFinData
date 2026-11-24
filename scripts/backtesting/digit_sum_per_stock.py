"""
Digit-sum, stock by stock (R&D #72 follow-up, owner 2026-11-24): for which stocks, if any, does the
live digit-sum score predict the next session out of sample? Pre-registered 2026-11-24.

Owner decision (2026-11-24): the digit-sum term is shadowed in the live Short10 / Long60 (weight 0)
except for the stocks this check confirms; it is re-checked every month. R&D #72 judged the score
across all stocks at once (REMOVE: no signal out of sample); the owner expects it to work for some
stocks only, which an all-stock average can hide.

WINDOWS (from AS_OF = the last SPY bar, unless --as-of is given)
  confirm   the last CONFIRM_MONTHS (12) months up to AS_OF
  discover  the DISCOVER_MONTHS (24) months before that
  fit       every bar before the discovery window: the index is rebuilt from these bars only,
            with the live construction (digit_sum_study bucket counts, the temporal check and the
            live gate scoring._build_digit_index, as R&D #72's digit_sum_holdout_study.py does)
  The first run (AS_OF 2026-11-23): fit < 2023-11-23, discover 2023-11-23..2025-11-22,
  confirm 2025-11-23..2026-11-23. Each monthly re-check moves all three forward.

PER STOCK, PER WINDOW
  Each session whose NEXT session falls in the window: score s = scoring.digit_sum_score(symbol,
  close) with the rebuilt index; when s != 0, the outcome is sign(s) x (up - base), up = next session
  closes above its open, base = the stock's own up rate over every session in the same window (the
  #72 lesson: an older base turns a rising market into a fake hit). Mean, plain t (next-session
  outcomes do not overlap), one-sided p for mean > 0.

DECISION RULE
  discovered  at least MIN_DISCOVER (30) signals in the discovery window, and Benjamini-Hochberg at
              BH_Q (10%) over the one-sided p of every stock with enough signals.
  confirmed   discovered, AND at least MIN_CONFIRM (15) signals in the confirmation window with
              mean > 0 and one-sided p < CONFIRM_P (0.05).
  The allowlist is the confirmed stocks. Only they keep the live digit-sum term (at the live
  weight); every other stock gets 0. An empty list means the term is shadowed for every stock.
  With no real signal, BH discovers about nothing and a chance discovery must also pass the
  confirmation year, so a long list would itself be a surprise worth checking.

THE MONTHLY RE-CHECK
  --write-allowlist writes Data/digit_sum_allowlist.json (this script is its only writer).
  --monthly runs only when that file is missing or STALE_DAYS (28) old; the weekly Saturday task
  calls it with both flags, so the check runs about once a month.

REPORTED, NOT JUDGED
  Signals and stocks tested per window; discovered and confirmed counts with each stock's numbers;
  the all-stock pooled excess in each window (R&D #72's measure (a), same-period base).

NOT MEASURED / BIASES
  The live score still reads the full-history index (Data/digit_sum_*study.json, last built
  2026-07-22), not the index rebuilt here; the allowlist only decides which stocks keep the term.
  Today's symbol list; next-session direction only (not the 10- or 60-day horizons the scores
  feed); no costs (the term only nudges a score, it is not a trade).

Usage (from a worktree, reading the real caches):
    AETHER_CACHE_DIR=<main>/Data python scripts/backtesting/digit_sum_per_stock.py [--write-allowlist] [--monthly]
"""
import argparse
import datetime
import glob
import json
import math
import multiprocessing
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

import _study_utils as su  # noqa: E402
import digit_sum_holdout_study as hs  # noqa: E402
from aether import paths, scoring  # noqa: E402

CONFIRM_MONTHS = 12
DISCOVER_MONTHS = 24
MIN_DISCOVER = 30
MIN_CONFIRM = 15
BH_Q = 0.10
CONFIRM_P = 0.05
STALE_DAYS = 28
ALLOWLIST_NAME = "digit_sum_allowlist.json"


def months_back(day: str, n: int) -> str:
    """The same calendar day n months earlier (clipped to the month's last day)."""
    d = datetime.date.fromisoformat(day)
    y, m = divmod(d.year * 12 + d.month - 1 - n, 12)
    m += 1
    last = (datetime.date(y + (m == 12), m % 12 + 1, 1) - datetime.timedelta(days=1)).day
    return datetime.date(y, m, min(d.day, last)).isoformat()


def windows(as_of: str) -> dict:
    confirm_start = months_back(as_of, CONFIRM_MONTHS)
    return {"as_of": as_of, "confirm_start": confirm_start,
            "discover_start": months_back(confirm_start, DISCOVER_MONTHS)}


def symbol_job(job):
    """Index rows from bars before discover_start (both digit kinds) and every (day, close, next up, next day)."""
    sym, ohlcv_dir, discover_start = job
    try:
        with open(os.path.join(ohlcv_dir, f"{sym}_daily.json"), encoding="utf-8") as f:
            ts = json.load(f).get("Time Series (Daily)", {})
    except (OSError, ValueError):
        return sym, None
    dates = sorted(ts)
    if len(dates) < 100:
        return sym, None
    cut = {d: v for d, v in ts.items() if d < discover_start}
    out = {"rows": {}, "recs": []}
    for kind, fn in hs.FNS.items():
        rows, _base = hs.bucket_rows(sym, ts, fn, cutoff=discover_start)
        for r in rows:
            if abs(r["z"]) >= hs.MIN_ABS_Z:
                r.update(su.window_analysis(r["digit"], r["z"], cut, fn, r["type"]))
        out["rows"][kind] = rows
    for i in range(len(dates) - 1):
        cur, nx = hs._oc(ts[dates[i]]), hs._oc(ts[dates[i + 1]])
        if cur is None or nx is None or dates[i + 1] < discover_start:
            continue
        out["recs"].append((dates[i], cur[1], int(nx[1] > nx[0]), dates[i + 1]))
    return sym, out


def stats(xs: list) -> dict:
    """n, mean, plain t and the one-sided p of mean > 0 (normal approximation)."""
    n = len(xs)
    if n < 2:
        return {"n": n, "mean": (xs[0] if xs else None), "t": None, "p": None}
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    t = m / (sd / math.sqrt(n)) if sd > 0 else None
    p = 0.5 * math.erfc(t / math.sqrt(2)) if t is not None else None
    return {"n": n, "mean": m, "t": t, "p": p}


def outcomes(sym: str, recs: list, start: str, end: str | None, score=None) -> list:
    """sign(s) x (up - base) for each signal whose next session is in [start, end); base = the stock's
    up rate over every such session (same period)."""
    score = score or scoring.digit_sum_score
    win = [(c, up) for _d, c, up, nd in recs if nd >= start and (end is None or nd < end)]
    if not win:
        return []
    base = sum(up for _c, up in win) / len(win)
    out = []
    for close, up in win:
        s = score(sym, close)
        if s:
            out.append((1 if s > 0 else -1) * (up - base))
    return out


def bh(pvals: dict, q: float = BH_Q) -> set:
    """Benjamini-Hochberg: the keys discovered at false-discovery rate q."""
    ranked = sorted((p, k) for k, p in pvals.items() if p is not None)
    m, keep = len(ranked), 0
    for i, (p, _k) in enumerate(ranked, 1):
        if p <= i / m * q:
            keep = i
    return {k for _p, k in ranked[:keep]}


def decide(disc: dict, conf: dict) -> dict:
    """disc / conf: {sym: stats()}. Returns tested, discovered and the confirmed allowlist."""
    tested = {s: st["p"] for s, st in disc.items() if st["n"] >= MIN_DISCOVER}
    found = bh(tested)
    allow = sorted(s for s in found
                   if (c := conf.get(s)) and c["n"] >= MIN_CONFIRM and c["mean"] is not None and c["mean"] > 0
                   and c["p"] is not None and c["p"] < CONFIRM_P)
    return {"tested": len(tested), "discovered": sorted(found), "allowlist": allow}


def is_stale(path: str, today: datetime.date) -> bool:
    try:
        with open(path, encoding="utf-8") as f:
            run = datetime.date.fromisoformat(json.load(f)["run_at"][:10])
    except (OSError, ValueError, KeyError, TypeError):
        return True
    return (today - run).days >= STALE_DAYS


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
    ap.add_argument("--as-of", default=None, help="YYYY-MM-DD (default: the last SPY bar)")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--max-symbols", type=int, default=None, help="smoke test on a slice (don't read results)")
    ap.add_argument("--write-allowlist", action="store_true")
    ap.add_argument("--monthly", action="store_true", help="skip unless the allowlist is missing or 28+ days old")
    a = ap.parse_args(argv)
    allow_path = os.path.join(paths.data_dir(), ALLOWLIST_NAME)
    if a.monthly and not is_stale(allow_path, datetime.date.today()):
        sys.stdout.write(f"digit-sum allowlist is fresh ({allow_path}); monthly check skipped\n")
        return 0
    ohlcv_dir = paths.ohlcv_dir()
    if a.as_of:
        as_of = a.as_of
    else:
        with open(os.path.join(ohlcv_dir, "SPY_daily.json"), encoding="utf-8") as f:
            as_of = max(json.load(f)["Time Series (Daily)"])
    w = windows(as_of)
    symbols = sorted(os.path.basename(f)[:-len("_daily.json")] for f in glob.glob(os.path.join(ohlcv_dir, "*_daily.json")))
    if a.max_symbols:
        symbols = symbols[:a.max_symbols]
    jobs = [(s, ohlcv_dir, w["discover_start"]) for s in symbols]
    with multiprocessing.Pool(a.workers) as pool:
        per_sym = {s: d for s, d in pool.imap_unordered(symbol_job, jobs, chunksize=4) if d}
    rows_int = [r for d in per_sym.values() for r in d["rows"]["int"]]
    rows_full = [r for d in per_sym.values() for r in d["rows"]["full"]]
    with hs.live_index(rows_int, rows_full) as (idx, idx_full):
        disc = {s: stats(outcomes(s, d["recs"], w["discover_start"], w["confirm_start"])) for s, d in per_sym.items()}
        conf = {s: stats(outcomes(s, d["recs"], w["confirm_start"], None)) for s, d in per_sym.items()}
        index_size = {"int": len(idx), "full": len(idx_full)}
    res = decide(disc, conf)
    pooled = {}
    for name, part in (("discover", disc), ("confirm", conf)):
        n = sum(st["n"] for st in part.values())
        tot = sum(st["mean"] * st["n"] for st in part.values() if st["mean"] is not None)
        pooled[name] = {"signals": n, "stocks_with_signals": sum(st["n"] > 0 for st in part.values()),
                        "excess_hit_pct": round(100 * tot / n, 3) if n else None}

    def show(s):
        d, c = disc[s], conf.get(s, {})
        return {"symbol": s, "discover": d, "confirm": c}
    rep = {"run_at": datetime.datetime.now().isoformat(timespec="seconds"), "windows": w,
           "symbols": len(per_sym), "smoke_test": bool(a.max_symbols), "index_kept": index_size,
           "rule": {"min_discover": MIN_DISCOVER, "bh_q": BH_Q, "min_confirm": MIN_CONFIRM, "confirm_p": CONFIRM_P},
           "tested": res["tested"], "discovered": [show(s) for s in res["discovered"]],
           "allowlist": res["allowlist"], "pooled": pooled}
    sys.stdout.write(json.dumps({k: v for k, v in rep.items() if k != "discovered"}, indent=1, default=str) + "\n")
    sys.stdout.write(f"discovered {len(res['discovered'])} of {res['tested']} tested; confirmed {len(res['allowlist'])}: "
                     f"{', '.join(res['allowlist']) or '(none)'}\n")
    if a.max_symbols:
        return 0
    out = os.path.join(paths.data_dir(), f"digit_sum_per_stock_{datetime.date.today().isoformat()}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1, default=str)
    sys.stdout.write(f"wrote {out}\n")
    if a.write_allowlist:
        tmp = allow_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"run_at": rep["run_at"], "windows": w, "rule": rep["rule"], "tested": res["tested"],
                       "symbols": res["allowlist"], "report": os.path.basename(out)}, f, indent=1)
        os.replace(tmp, allow_path)
        sys.stdout.write(f"wrote {allow_path}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
