"""
OHLCV history recovery via Alpha Vantage / RapidAPI.

Normal daily closes come from Chaikin (PowerGauge) and are appended directly
in powergauge.py — zero extra API calls.

This module handles recovery only:
  - Missing Symbol_full/{sym}_daily.json files  → full fetch
  - Corrupted files (bad JSON, missing key)      → full fetch
  - Files with gap > MAX_GAP_DAYS               → compact fetch (last 100 days merged)

Usage:
    python rapidapi.py               # repair all symbols from Research sheet
    python rapidapi.py AAPL MSFT     # repair specific symbols

Env vars:
    RAPIDAPI_KEY   RapidAPI key for Alpha Vantage (required)
"""

import datetime
import json
import os
import sys
import time

import requests

RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY", "")
OHLCV_DIR    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Data", "Symbol_full")
MAX_GAP_DAYS = 30   # trigger compact/full fetch if latest entry is this many calendar days behind
SLEEP_SEC    = 14   # 14 s between requests → 4.3 req/min (safe under 5/min limit)

_BASE_URL = "https://alpha-vantage.p.rapidapi.com/query"
_HEADERS  = {
    "X-RapidAPI-Host": "alpha-vantage.p.rapidapi.com",
}


# ── Internal helpers ──────────────────────────────────────────────────────────

def _load_cache(path: str) -> dict | None:
    """Return full JSON dict or None if file missing/corrupt/missing key."""
    try:
        with open(path) as f:
            data = json.load(f)
        if not isinstance(data.get("Time Series (Daily)"), dict):
            return None
        return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _latest_date(ts: dict) -> str | None:
    return max(ts.keys()) if ts else None


def _needs_recovery(path: str, today_str: str) -> bool:
    """Check if this file needs a RapidAPI call. Never touches the network."""
    cache = _load_cache(path)
    if cache is None:
        return True
    ts = cache["Time Series (Daily)"]
    latest = _latest_date(ts)
    if not latest:
        return True
    gap = (datetime.date.fromisoformat(today_str) - datetime.date.fromisoformat(latest)).days
    return gap > MAX_GAP_DAYS


def _fetch_raw(symbol: str, outputsize: str = "compact") -> dict:
    """Single Alpha Vantage HTTP call. Returns parsed JSON. Raises on error."""
    if not RAPIDAPI_KEY:
        raise RuntimeError("RAPIDAPI_KEY env var not set — cannot fetch from RapidAPI")
    resp = requests.get(
        _BASE_URL,
        headers={**_HEADERS, "X-RapidAPI-Key": RAPIDAPI_KEY},
        params={
            "function": "TIME_SERIES_DAILY",
            "symbol": symbol,
            "outputsize": outputsize,
            "datatype": "json",
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if "Time Series (Daily)" not in data:
        note = data.get("Note") or data.get("Information") or str(data)[:120]
        raise RuntimeError(f"Alpha Vantage error for {symbol}: {note}")
    return data


def _write_atomic(path: str, data: dict) -> None:
    """Write JSON atomically via temp file + rename (safe on Windows)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def _fetch_and_merge(symbol: str, path: str, outputsize: str = "compact") -> None:
    """Fetch from RapidAPI and merge into existing file (or write fresh)."""
    raw = _fetch_raw(symbol, outputsize)

    if outputsize == "full" or not os.path.exists(path):
        _write_atomic(path, raw)
        return

    existing = _load_cache(path)
    if existing is None:
        _write_atomic(path, raw)
        return

    new_ts      = raw["Time Series (Daily)"]
    existing_ts = existing["Time Series (Daily)"]
    added = {d: v for d, v in new_ts.items() if d not in existing_ts}
    if not added:
        return  # nothing new to merge

    existing_ts.update(added)
    latest = max(existing_ts.keys())
    existing.setdefault("Meta Data", {})["3. Last Refreshed"] = latest
    _write_atomic(path, existing)


# ── Public API ────────────────────────────────────────────────────────────────

def repair_missing(symbols: list[str], today_str: str) -> dict:
    """
    Scan Symbol_full/ and repair symbols with missing, corrupted, or stale files.
    Uses compact fetch (last 100 days) when file exists but has a gap; full fetch
    when file is absent or corrupt. Skips symbols that already have today's close
    (written by powergauge._append_ohlcv_entry).

    Rate: 14 s sleep between API calls → ≤5 req/min.
    """
    results = {"updated": 0, "skipped": 0, "errors": []}

    for i, sym in enumerate(symbols, 1):
        path = os.path.join(OHLCV_DIR, f"{sym}_daily.json")
        if not _needs_recovery(path, today_str):
            results["skipped"] += 1
            continue

        existing = _load_cache(path)
        outputsize = "full" if existing is None else "compact"
        try:
            _fetch_and_merge(sym, path, outputsize=outputsize)
            results["updated"] += 1
            print(f"  [RapidAPI] {sym}: {outputsize} fetch OK "
                  f"({i}/{len(symbols)}, updated={results['updated']})")
            time.sleep(SLEEP_SEC)
        except Exception as e:
            results["errors"].append((sym, str(e)))
            print(f"  [RapidAPI] {sym}: ERROR — {e}")

    return results


# ── Legacy helpers (kept for compatibility) ───────────────────────────────────

def get_data(symbol: str, outputsize: str = "compact") -> str:
    """Low-level fetch — returns raw response text."""
    if not RAPIDAPI_KEY:
        raise RuntimeError("RAPIDAPI_KEY env var not set")
    resp = requests.get(
        _BASE_URL,
        headers={**_HEADERS, "X-RapidAPI-Key": RAPIDAPI_KEY},
        params={
            "function": "TIME_SERIES_DAILY",
            "symbol": symbol,
            "outputsize": outputsize,
            "datatype": "json",
        },
        timeout=30,
    )
    return resp.text


def get_quotes(time_frame, year=2022, month=1, day=1, symbol='MSFT'):
    import numpy as np
    result = []
    if time_frame == 'D1':
        path = os.path.join(OHLCV_DIR, f"{symbol}_daily.json")
        with open(path) as f:
            data = json.load(f).get('Time Series (Daily)', {})
        cutoff = f"{year}-{month:02d}-{day:02d}"
        for date_key, value in data.items():
            if date_key <= cutoff:
                break
            result.insert(0, [
                float(value.get("1. open",  0)),
                float(value.get("2. high",  0)),
                float(value.get("3. low",   0)),
                float(value.get("4. close", 0)),
            ])
    return np.array(result)


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    today_str = str(datetime.date.today())

    if len(sys.argv) > 1:
        # Explicit symbols passed on command line
        syms = [s.upper() for s in sys.argv[1:]]
    else:
        # Load all symbols from Research sheet
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from run_history import load_symbols
        syms = load_symbols()

    print(f"[RapidAPI] Recovery pass — {len(syms)} symbols, today={today_str}")
    result = repair_missing(syms, today_str)
    print(f"\n[RapidAPI] Done: {result['updated']} fetched, "
          f"{result['skipped']} already current, "
          f"{len(result['errors'])} errors")
    if result["errors"]:
        for sym, err in result["errors"]:
            print(f"  ERROR {sym}: {err}")
