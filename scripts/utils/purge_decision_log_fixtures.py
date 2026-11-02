"""Remove test-fixture rows from Data/decision_log.jsonl.

Before the test suite stopped writing to the real decision log (tests/__init__.py), test runs
appended fixture decisions to it: 1,279 ULTA rows priced 95-450 when ULTA traded near $550, and
260 GMED rows at exactly 72.00 / cost 74.80 for 11 days. The scorecard scored them.

A row is a fixture when either:
- its logged price is more than MAX_PRICE_GAP from the latest OHLCV close on or before its date
  (the pipeline also runs on weekends; a real decision logs the live quote, within a few percent
  of that close; rows with no earlier close are kept); or
- the same symbol, price and cost appear on REPEAT_DATES or more different dates. A real quote
  moves every trading day; a weekend repeats Friday's for at most about three dates.

Run it when no AETHER task is running. The trade run appends to this log; --apply aborts if
the file changes between the read and the write.

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
REPEAT_DATES = 5       # on the PROD log only the GMED fixture repeats, even at 3 dates


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


def _signature(entry):
    return entry.get("symbol"), entry.get("price"), entry.get("cost")


def repeated_signatures(entries) -> set:
    """(symbol, price, cost) logged on REPEAT_DATES or more different dates."""
    dates = collections.defaultdict(set)
    for e in entries:
        if e.get("price") is not None:
            dates[_signature(e)].add(e.get("date"))
    return {sig for sig, ds in dates.items() if len(ds) >= REPEAT_DATES}


def split(entries):
    cache, keep, drop = {}, [], []
    repeated = repeated_signatures(entries)
    for e in entries:
        bad = _signature(e) in repeated or is_fixture(e, _closes(e.get("symbol"), cache))
        (drop if bad else keep).append(e)
    return keep, drop


def _count_lines(path) -> int:
    with open(path, encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="back up the log, then rewrite it")
    ap.add_argument("--log", default=None, help="decision log path (default Data/decision_log.jsonl)")
    args = ap.parse_args(argv)
    path = args.log or str(decision_eval.LOG)

    before = os.stat(path)
    lines = _count_lines(path)
    entries = decision_eval.read_log(path)
    keep, drop = split(entries)
    malformed = lines - len(entries)
    by_sym = collections.Counter(e.get("symbol") for e in drop)
    sys.stdout.write(f"{path}: {lines} lines, {len(entries)} rows, {len(drop)} fixture rows, {len(keep)} kept"
                     f"{f', {malformed} malformed line(s) also dropped' if malformed else ''}\n")
    for sym, n in by_sym.most_common():
        sys.stdout.write(f"  {sym}: {n}\n")
    if not args.apply or not (drop or malformed):
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
    now = os.stat(path)
    if (now.st_size, now.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
        os.remove(tmp)
        sys.stdout.write("The log changed while this ran (an AETHER task appended to it); nothing "
                         "written. Run it again when no task is running.\n")
        return 1
    os.replace(tmp, path)
    sys.stdout.write(f"Backed up to {backup}; removed {len(drop)} rows"
                     f"{f' and {malformed} malformed line(s)' if malformed else ''}.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
