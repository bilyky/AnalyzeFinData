"""
snapshot_integrity.py — find and quarantine Chaikin snapshots that don't hold their date's data.

Two kinds, both checked on the dev cache on 2026-11-04:

1. Wrong-date backfills. The Chaikin API serves current values only (it ignores dates), but
   run_history used to fill missing past days by fetching live and saving under the old
   date. One symbol's files then carry the same current values across many dates. On dev
   ~114k of ~570k files were written more than 3 days after their date, in bursts
   (2026-04-26/27, 05-07..09, 07-24). In a 60-symbol sample, 93-99% of the April/May ones
   had a price more than 2% off RapidAPI's close for their date (GNRC 2023-09-01: $266.75
   vs a real $118.94), and their PGR was one constant value. The 07-24 batch had prices
   repaired but the same constant PGR. Files written on time agree with Chaikin's own daily
   PGR history (the chart backfill) 98-100% of the time.

2. Weekday-holiday snapshots (NYSE closed per aether.market_calendar): written by a run on
   the holiday, holding the last close, e.g. 2026-05-25 with junk "-1-1-..." signals.
   Weekend files are left alone: the evening report runs daily and reads them.

Detection of (1) uses content, not file times alone (another machine's copy may have
different times):
  - a "late batch" = one symbol's files written on one day, more than LATE_DAYS after the
    dates they claim, covering at least BATCH_MIN dates;
  - each file is checked where possible: price vs the RapidAPI close for its date
    (Data/Symbol_full), and corrected PGR vs the chart's dailyPgr for the session before
    (Data/Symbol_chart, 7->5 levels; run chaikin_chart_backfill.py first for full coverage);
  - the batch is wrong-date when at least half of its price checks fail, or at least a
    fifth of its PGR checks (at least 5). A backfill writes the same values into every file,
    so the whole batch goes.
Late files that can't be checked are counted as "unverified" and left in place.

Dry run by default (prints counts by write date). --apply MOVES the files to
Data/Backup/snapshot_quarantine_<time>/<kind>/<SYM>/ with manifest.json (move back to undo).

Usage (repo root):
    python scripts/utils/snapshot_integrity.py
    python scripts/utils/snapshot_integrity.py --apply
"""
import argparse
import datetime
import json
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from aether import market_calendar, paths  # noqa: E402

NAME_RE = re.compile(r"^(?P<sym>.+)_(?P<day>\d{4}-\d{2}-\d{2})\.json$")
LATE_DAYS = 3          # written more than this many days after its date = a late file
BATCH_MIN = 3          # a late batch covers at least this many dates
PRICE_TOL = 0.02       # price off RapidAPI's close by more than this fails
PRICE_BAD = 0.5        # a batch is wrong-date when half its price checks fail ...
PGR_BAD = 0.2          # ... or a fifth of its PGR checks (files written on time agree 98-100%)
PGR_MIN = 5            # the PGR share needs at least this many checks
PGR_7_TO_5 = {1: 1, 2: 2, 3: 3, 4: 3, 5: 3, 6: 4, 7: 5}


def _out(msg: str = "") -> None:
    sys.stdout.write(msg + "\n")


def _read(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def is_weekday_holiday(day: str) -> bool:
    d = datetime.date.fromisoformat(day)
    return d.weekday() < 5 and not market_calendar.is_session(d)


def rapid_closes(symbol: str) -> dict:
    ts = (_read(os.path.join(paths.ohlcv_dir(), f"{symbol}_daily.json")) or {}).get("Time Series (Daily)") or {}
    return {d: float(v["4. close"]) for d, v in ts.items() if not v.get("provisional") and v.get("4. close")}


def chart_prev_pgr(symbol: str) -> dict:
    """{date: the chart's dailyPgr of the session before, on the 5-level scale}."""
    rows = (_read(os.path.join(paths.cache_dir(), "Symbol_chart", f"{symbol}_chart.json")) or {}).get("rows") or {}
    days = sorted(rows)
    return {d: PGR_7_TO_5.get(rows[p].get("dailyPgr")) for p, d in zip(days, days[1:])}


def file_checks(bundle: dict, day: str, closes: dict, prev_pgr: dict) -> dict:
    """{"price": True/False, "pgr": True/False} for the checks that can be made."""
    out = {}
    try:
        last = float(((bundle.get("metaInfo") or [{}])[0] or {}).get("Last"))
    except (TypeError, ValueError):
        last = None
    if last and closes.get(day):
        out["price"] = abs(last / closes[day] - 1) <= PRICE_TOL
    pgr = {k: v for e in bundle.get("pgr") or [] for k, v in e.items()}.get("Corrected PGR Value")
    if prev_pgr.get(day) and pgr:
        out["pgr"] = pgr == prev_pgr[day]
    return out


def batch_is_wrong(checks: list) -> bool:
    """checks: one file_checks() dict per file of a late batch."""
    price = [c["price"] for c in checks if "price" in c]
    pgr = [c["pgr"] for c in checks if "pgr" in c]
    return bool((price and price.count(False) >= PRICE_BAD * len(price))
                or (len(pgr) >= PGR_MIN and pgr.count(False) >= PGR_BAD * len(pgr)))


def scan_symbol(base: str, symbol: str) -> dict:
    """{"holiday": [paths], "wrong_date": [paths], "unverified": n, "late": n, "late_passed": n}."""
    d = os.path.join(base, symbol)
    files = []
    for f in os.listdir(d):
        m = NAME_RE.match(f)
        if m:
            p = os.path.join(d, f)
            files.append((p, m["day"], datetime.date.fromtimestamp(os.stat(p).st_mtime)))
    res = {"holiday": [p for p, day, _w in files if is_weekday_holiday(day)], "wrong_date": [],
           "unverified": 0, "late": 0, "late_passed": 0}
    batches: dict = {}
    for p, day, w in files:
        if (w - datetime.date.fromisoformat(day)).days > LATE_DAYS:
            batches.setdefault(w, []).append((p, day))
    res["late"] = sum(len(b) for b in batches.values())
    if not batches:
        return res
    closes, prev_pgr = rapid_closes(symbol), chart_prev_pgr(symbol)
    for batch in batches.values():
        checks = [c for c in (file_checks(_read(p) or {}, day, closes, prev_pgr) for p, day in batch) if c]
        if len({day for _p, day in batch}) >= BATCH_MIN and batch_is_wrong(checks):
            res["wrong_date"] += [p for p, _day in batch if p not in res["holiday"]]
        elif not checks:
            res["unverified"] += len(batch)
        else:
            res["late_passed"] += len(batch)
    return res


def scan(base: str) -> dict:
    total = {"holiday": [], "wrong_date": [], "unverified": 0, "late": 0, "late_passed": 0, "files": 0}
    for sym in sorted(os.listdir(base)):
        if not os.path.isdir(os.path.join(base, sym)):
            continue
        r = scan_symbol(base, sym)
        total["files"] += sum(1 for f in os.listdir(os.path.join(base, sym)) if NAME_RE.match(f))
        for k in ("holiday", "wrong_date"):
            total[k] += r[k]
        for k in ("unverified", "late", "late_passed"):
            total[k] += r[k]
    return total


def quarantine(paths_by_kind: dict, base: str, dest: str) -> dict:
    moved = []
    for kind, items in paths_by_kind.items():
        for p in items:
            rel = os.path.relpath(p, base)
            target = os.path.join(dest, kind, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.move(p, target)
            moved.append({"kind": kind, "from": rel})
    with open(os.path.join(dest, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"source": base, "moved": moved, "written": datetime.datetime.now().isoformat(timespec="seconds")},
                  f, indent=1)
    return {"moved": len(moved), "dest": dest}


def _by_write_date(items: list) -> dict:
    out: dict = {}
    for p in items:
        w = datetime.date.fromtimestamp(os.stat(p).st_mtime).isoformat()
        out[w] = out.get(w, 0) + 1
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--apply", action="store_true", help="move the files (default: dry run)")
    a = ap.parse_args(argv)
    base = paths.symbol_dir()
    r = scan(base)
    _out(f"{r['files']} snapshot files; {r['late']} written more than {LATE_DAYS} days after their date")
    _out(f"wrong-date (late batches failing price/PGR checks): {len(r['wrong_date'])}")
    for w, n in sorted(_by_write_date(r["wrong_date"]).items(), key=lambda x: -x[1])[:10]:
        _out(f"  written {w}: {n}")
    _out(f"weekday-holiday snapshots: {len(r['holiday'])}")
    _out(f"late but unverifiable (no RapidAPI close and no chart row), left in place: {r['unverified']}")
    _out(f"late batches that passed the checks (or too few dates), left in place: {r['late_passed']}")
    if not (r["wrong_date"] or r["holiday"]):
        return 0
    if not a.apply:
        _out("dry run: add --apply to move them to Data/Backup/")
        return 0
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    q = quarantine({"wrong_date": r["wrong_date"], "holiday": r["holiday"]}, base,
                   os.path.join(paths.data_dir(), "Backup", f"snapshot_quarantine_{stamp}"))
    _out(f"moved {q['moved']} files to {q['dest']} (manifest.json lists them)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
