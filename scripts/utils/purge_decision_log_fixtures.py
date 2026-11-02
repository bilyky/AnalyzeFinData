"""Remove test-fixture rows from Data/decision_log.jsonl.

Before the test suite stopped writing to the real decision log (tests/__init__.py), test runs
appended fixture decisions to it: 1,279 ULTA rows priced 95-450 when ULTA traded near $550.
The scorecard scored them (+480% 10-day "returns").

A row is a fixture when its logged price is more than MAX_PRICE_GAP from the latest OHLCV close
on or before its date (the pipeline also runs on weekends). A real decision logs the live quote,
which stays within a few percent of that close. Rows with no earlier close are kept.

    python scripts/utils/purge_decision_log_fixtures.py            # dry run: counts by symbol
    python scripts/utils/purge_decision_log_fixtures.py --apply    # backs up, then rewrites
"""

import argparse
import collections
import datetime
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from aether import decision_eval, paths

MAX_PRICE_GAP = 0.15   # fixtures sat 19%+ off; a 10% cut also drops real rows (PROD log)


def _closes(symbol, cache):
    if symbol not in cache:
        try:
            with open(os.path.join(paths.ohlcv_dir(), f"{symbol}_daily.json"), encoding="utf-8") as f:
                ts = json.load(f).get("Time Series (Daily)", {})
            cache[symbol] = {d: float(b["4. close"]) for d, b in ts.items()}
        except (OSError, ValueError, KeyError, TypeError):
            cache[symbol] = {}
    return cache[symbol]


def close_on_or_before(closes, day):
    """Latest close dated on or before day, or None."""
    if not day:
        return None
    past = [d for d in closes if d <= day]
    return closes[max(past)] if past else None


def is_fixture(entry, closes) -> bool:
    """True when the logged price is more than MAX_PRICE_GAP from the latest close."""
    close = close_on_or_before(closes, entry.get("date"))
    price = entry.get("price")
    if not close or not price:
        return False
    return abs(price - close) / close > MAX_PRICE_GAP


def split(entries):
    cache, keep, drop = {}, [], []
    for e in entries:
        (drop if is_fixture(e, _closes(e.get("symbol"), cache)) else keep).append(e)
    return keep, drop


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="back up the log, then rewrite it")
    ap.add_argument("--log", default=None, help="decision log path (default Data/decision_log.jsonl)")
    args = ap.parse_args(argv)
    path = args.log or str(decision_eval.LOG)

    entries = decision_eval.read_log(path)
    keep, drop = split(entries)
    by_sym = collections.Counter(e.get("symbol") for e in drop)
    sys.stdout.write(f"{path}: {len(entries)} rows, {len(drop)} fixture rows, {len(keep)} kept\n")
    for sym, n in by_sym.most_common():
        sys.stdout.write(f"  {sym}: {n}\n")
    if not args.apply or not drop:
        sys.stdout.write("Dry run: nothing written.\n" if not args.apply else "Nothing to remove.\n")
        return 0

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(paths.data_dir(), "Backup", f"{stamp}_decision_log")
    os.makedirs(backup_dir, exist_ok=True)
    backup = os.path.join(backup_dir, os.path.basename(path))
    shutil.copy2(path, backup)
    if os.path.getsize(backup) != os.path.getsize(path):
        sys.stdout.write(f"Backup size mismatch at {backup}; nothing written.\n")
        return 1
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for e in keep:
            f.write(json.dumps(e) + "\n")
    os.replace(tmp, path)
    sys.stdout.write(f"Backed up to {backup}; removed {len(drop)} rows.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
