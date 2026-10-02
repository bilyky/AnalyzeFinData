"""
One-off cleanup: delete weekend-dated placeholder bars from Data/Symbol_full/*_daily.json.

A placeholder (bar_provenance.is_provisional) dated Saturday/Sunday is never a session, so
the RapidAPI repair can never replace it. Until now powergauge wrote one for every weekend
run, and they sat in the series as flat zero-volume bars shrinking ATR. The writer now skips
weekends and the merge settles in-window placeholders, so this only clears the backlog.

Weekday placeholders are NOT touched here: their close is real, and the next compact
RapidAPI fetch (rapidapi.py, nightly) replaces each with the settled bar. The report counts
them so you can see what is left for that pass.

Dry run by default. --apply first copies every file it will change into
Data/Backup/<timestamp>_weekend_placeholders/Symbol_full/, then rewrites it atomically.

Usage:
    python scripts/sync/prune_weekend_placeholders.py            # dry run
    python scripts/sync/prune_weekend_placeholders.py --apply
"""
import argparse
import datetime
import glob
import json
import os
import shutil
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE_DIR)

from bar_provenance import is_provisional, is_weekend


def _out(line: str = "") -> None:
    """Report line — intentional CLI output, unprefixed."""
    sys.stdout.write(line + "\n")


def weekend_placeholders(ts: dict) -> list[str]:
    return sorted(d for d, bar in ts.items() if is_provisional(bar) and is_weekend(d))


def prune_file(path: str, backup_dir: str | None) -> tuple[int, int]:
    """(weekend placeholders removed-or-found, weekday placeholders left). Writes only when
    backup_dir is given, after copying the original there."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    ts = data.get("Time Series (Daily)") or {}
    drop = weekend_placeholders(ts)
    weekday_left = sum(1 for d, bar in ts.items() if is_provisional(bar) and not is_weekend(d))
    if drop and backup_dir:
        shutil.copy2(path, os.path.join(backup_dir, os.path.basename(path)))
        for d in drop:
            del ts[d]
        if ts:
            data.setdefault("Meta Data", {})["3. Last Refreshed"] = max(ts)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    return len(drop), weekday_left


def run(data_dir: str, apply: bool) -> dict:
    files = sorted(glob.glob(os.path.join(data_dir, "Symbol_full", "*_daily.json")))
    backup_dir = None
    if apply:
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = os.path.join(data_dir, "Backup", f"{stamp}_weekend_placeholders", "Symbol_full")
        os.makedirs(backup_dir, exist_ok=True)
    removed = weekday = changed = 0
    errors = []
    for path in files:
        try:
            n, left = prune_file(path, backup_dir)
        except (OSError, json.JSONDecodeError) as e:
            errors.append(f"{os.path.basename(path)}: {type(e).__name__}: {e}")
            continue
        removed += n
        weekday += left
        changed += bool(n)
    verb = "removed" if apply else "would remove"
    _out(f"\n{len(files)} files: {verb} {removed} weekend placeholder bars from {changed} files")
    _out(f"weekday placeholders left for the nightly RapidAPI compact fetch: {weekday}")
    if backup_dir:
        _out(f"originals backed up to {backup_dir}")
    for e in errors:
        _out(f"  ERROR {e}")
    return {"files": len(files), "removed": removed, "changed_files": changed,
            "weekday_left": weekday, "backup_dir": backup_dir, "errors": errors}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Delete weekend-dated OHLCV placeholder bars")
    ap.add_argument("--data-dir", default=os.path.join(BASE_DIR, "Data"))
    ap.add_argument("--apply", action="store_true", help="write changes (after backing up)")
    a = ap.parse_args()
    run(a.data_dir, a.apply)
