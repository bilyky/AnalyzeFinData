"""
pgr_factor_repair.py — find and repair cached Chaikin snapshots that have no PGR factors.

From 2026-09-01 the /api/suggestions adapter wrote the four factor lists (Financials,
Earnings, Technicals, Experts: the 20 factors plus 4 group scores) as empty. The adapter now
maps them. This script:

  audit    (default) coverage per session: stocks cached vs stocks with factors. ETFs and
           invalid symbols don't count.
  --mark   writes Data/pgr_factor_gap.json: the sessions with no factors, so studies can
           treat them as missing (not as zero).
  --refetch-today  re-fetches today's snapshots that lack factors. Dry run unless --apply;
           backs up the files first. Only today: the API serves current values, so the
           missed sessions (2026-09-01 onward) can't be refilled.
  --check  exit code 1 when the latest session's coverage is below --min (default 90%).

Usage (repo root, on PROD):
    python scripts/utils/pgr_factor_repair.py --since 2026-08-25
    python scripts/utils/pgr_factor_repair.py --since 2026-08-25 --mark
    python scripts/utils/pgr_factor_repair.py --refetch-today            # dry run
    python scripts/utils/pgr_factor_repair.py --refetch-today --apply    # after the close
    python scripts/utils/pgr_factor_repair.py --check
"""
import argparse
import datetime
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import powergauge  # noqa: E402
from aether import market_calendar, paths  # noqa: E402

GROUPS = ("Financials", "Earnings", "Technicals", "Experts")


def _out(msg: str = "") -> None:
    sys.stdout.write(msg + "\n")


def has_factors(bundle: dict):
    """True / False for a stock snapshot; None when it doesn't count (ETF, invalid, unreadable)."""
    # "success" = the legacy getSymbolData status (snapshots up to 2026-08-31), "ok" = the adapter's.
    if not isinstance(bundle, dict) or bundle.get("status") not in ("ok", "success"):
        return None
    pgr = bundle.get("pgr") or []
    meta = (bundle.get("metaInfo") or [{}])[0]
    if len(pgr) < 7 or pgr[6].get("is_etf_symbol") or meta.get("is_etf_symbol"):
        return None
    return any(pgr[i].get(g) for i, g in enumerate(GROUPS, start=1))


def _read(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def sessions(since: datetime.date, until: datetime.date) -> list:
    out, d = [], since
    while d <= until:
        if market_calendar.is_session(d):
            out.append(d.isoformat())
        d += datetime.timedelta(days=1)
    return out


def coverage(base: str, symbols: list, day: str) -> dict:
    """{"stocks": n, "with": n, "missing": [symbols]} for one session."""
    stocks, missing = 0, []
    for s in symbols:
        b = _read(os.path.join(base, s, f"{s}_{day}.json"))
        ok = has_factors(b) if b is not None else None
        if ok is None:
            continue
        stocks += 1
        if not ok:
            missing.append(s)
    return {"stocks": stocks, "with": stocks - len(missing), "missing": missing}


def audit(base: str, since: datetime.date, until: datetime.date) -> list:
    symbols = sorted(d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d)))
    rows = []
    for day in sessions(since, until):
        c = coverage(base, symbols, day)
        if c["stocks"]:
            rows.append({"date": day, **c})
    return rows


def gap_days(rows: list, min_pct: float) -> list:
    return [r["date"] for r in rows if r["with"] < min_pct * r["stocks"]]


def _fetch_live(symbol: str, day: datetime.date):
    return powergauge.get_symbol_data(symbol, day, prefer_cache=False)


def refetch_today(base: str, symbols: list, apply: bool, fetch=None) -> dict:
    """Re-fetch today's snapshots that lack factors. ``fetch(symbol, date)`` defaults to
    powergauge.get_symbol_data (which refuses to cache while NYSE is open)."""
    today = datetime.date.today().isoformat()
    todo = coverage(base, symbols, today)["missing"]
    res = {"date": today, "todo": todo, "fixed": [], "still_missing": [], "backup": None}
    if not apply or not todo:
        return res
    fetch = fetch or _fetch_live
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bdir = os.path.join(paths.data_dir(), "Backup", f"pgr_refetch_{stamp}")
    os.makedirs(bdir, exist_ok=True)
    for s in todo:
        shutil.copy2(os.path.join(base, s, f"{s}_{today}.json"), bdir)
    res["backup"] = bdir
    for s in todo:
        try:
            fetch(s, datetime.date.today())
        except Exception as e:  # one symbol's failure shouldn't stop the rest
            _out(f"  {s}: fetch failed ({e})")
        ok = has_factors(_read(os.path.join(base, s, f"{s}_{today}.json")))
        (res["fixed"] if ok else res["still_missing"]).append(s)
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--since", type=datetime.date.fromisoformat, default=datetime.date(2026, 8, 25))
    ap.add_argument("--until", type=datetime.date.fromisoformat, default=datetime.date.today())
    ap.add_argument("--min", type=float, default=0.90, help="coverage below this = a gap session")
    ap.add_argument("--mark", action="store_true", help="write Data/pgr_factor_gap.json")
    ap.add_argument("--refetch-today", action="store_true")
    ap.add_argument("--apply", action="store_true", help="with --refetch-today: actually re-fetch")
    ap.add_argument("--check", action="store_true", help="exit 1 if the latest session is a gap")
    a = ap.parse_args(argv)
    base = paths.symbol_dir()

    if a.refetch_today:
        symbols = sorted(d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d)))
        r = refetch_today(base, symbols, a.apply)
        _out(f"{r['date']}: {len(r['todo'])} stock snapshots without factors")
        if not a.apply:
            _out("dry run: add --apply to re-fetch them (after the close)")
        else:
            _out(f"backup: {r['backup']}\nfixed {len(r['fixed'])}, still missing {len(r['still_missing'])}: "
                  f"{', '.join(r['still_missing'][:20])}")
        return 0

    since = a.until - datetime.timedelta(days=7) if a.check else a.since
    rows = audit(base, since, a.until)
    for r in rows:
        flag = "  GAP" if r["with"] < a.min * r["stocks"] else ""
        _out(f"{r['date']}  stocks {r['stocks']:4d}  with factors {r['with']:4d}{flag}")
    gaps = gap_days(rows, a.min)
    _out(f"\n{len(gaps)} gap sessions" + (f": {gaps[0]} .. {gaps[-1]}" if gaps else ""))
    if a.mark:
        out = os.path.join(paths.data_dir(), "pgr_factor_gap.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump({"note": "Sessions whose cached Chaikin snapshots have no PGR factors (adapter bug "
                               "2026-09-01, fixed by the pgrData mapping). Treat as missing, not zero.",
                       "min_coverage": a.min, "gap_sessions": gaps,
                       "written": datetime.datetime.now().isoformat(timespec="seconds")}, f, indent=1)
        _out(f"written: {out}")
    if a.check:
        if not rows:
            _out("CHECK: no snapshots in the last 7 days")
            return 1
        latest = rows[-1]
        if latest["with"] < a.min * latest["stocks"]:
            _out(f"CHECK FAILED: {latest['date']} has factors for {latest['with']}/{latest['stocks']} stocks")
            return 1
        _out(f"CHECK OK: {latest['date']} {latest['with']}/{latest['stocks']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
