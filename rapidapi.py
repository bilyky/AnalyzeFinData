"""
OHLCV history recovery via Alpha Vantage / RapidAPI.

Normal daily closes come from Chaikin (PowerGauge) and are appended directly
in powergauge.py — zero extra API calls.

This module handles recovery only:
  - Missing Symbol_full/{sym}_daily.json files  → full fetch
  - Corrupted files (bad JSON, missing key)      → full fetch
  - Files with gap > MAX_GAP_DAYS               → compact fetch (last 100 days merged)
  - Latest bar is provisional (Chaikin close-only, volume 0) → compact fetch to
    overwrite it with settled real-volume OHLCV (see bar_provenance.is_provisional)

Usage:
    python rapidapi.py                 # repair all symbols from Research sheet
    python rapidapi.py AAPL MSFT       # repair specific symbols (only if stale/gapped)
    python rapidapi.py INTC --force    # fetch even when the file isn't stale (re-test)

Config (in order of precedence):
    1. RAPIDAPI_KEY env var
    2. config.json  {"rapidapi": {"api_key": "..."}}  (copy from config.json.example)
"""

import datetime
import json
import os
import sys
import time

import numpy as np
import requests

from aether.logger import get_logger as _get_logger
from bar_provenance import is_provisional
from config import CFG
from run_history import load_symbols

_log = _get_logger("rapidapi")

_DIR      = os.path.dirname(os.path.abspath(__file__))
OHLCV_DIR = os.path.join(_DIR, "Data", "Symbol_full")
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


# The is_provisional predicate lives in the stdlib-only leaf module ``bar_provenance`` and is
# imported above so both this recovery layer and the pattern/volume consumers share one
# definition without importing each other.


def _check_recovery(path: str, today_str: str) -> tuple[bool, dict | None]:
    """Return (needs_repair, cache_or_None). Loads the file once; caller reuses the cache."""
    cache = _load_cache(path)
    if cache is None:
        return True, None
    ts = cache["Time Series (Daily)"]
    latest = _latest_date(ts)
    if not latest:
        return True, None
    gap = (datetime.date.fromisoformat(today_str) - datetime.date.fromisoformat(latest)).days
    if gap > MAX_GAP_DAYS:
        return True, cache
    # A provisional (Chaikin close-only) latest bar has no real volume/range, so repair it
    # even though its date is current (gap == 0) — this is what the old gap-only gate missed.
    # Any bar with real volume (volume > 0) is trusted and skipped.
    if is_provisional(ts[latest]):
        return True, cache
    return False, cache


_SYMBOL_OVERRIDES = {
    "IAC": "IACVV",
}

# Index pseudo-tickers that Alpha Vantage's TIME_SERIES_DAILY endpoint cannot serve.
# None are in the traded universe today; the set only documents intent. Do NOT add real
# equity tickers here — e.g. COMP is Compass, Inc. (a live NYSE stock with real-volume
# history in the universe), NOT the Nasdaq Composite; excluding it silently starves it of repair.
_UNSUPPORTED_SYMBOLS = {"NDX", "SPX", "DJI"}

def _fetch_raw(symbol: str, outputsize: str = "compact") -> dict:
    """Single Alpha Vantage HTTP call. Returns parsed JSON. Raises on error."""
    key = CFG.rapidapi_key
    if not key:
        raise RuntimeError(
            "RapidAPI key not configured. Set RAPIDAPI_KEY env var "
            "or add rapidapi.api_key to config.json."
        )

    # Resolve any Alpha Vantage-specific symbol overrides or formatting quirks
    api_symbol = _SYMBOL_OVERRIDES.get(symbol.upper(), symbol)
    api_symbol = api_symbol.replace(".", "-")

    resp = requests.get(
        _BASE_URL,
        headers={**_HEADERS, "X-RapidAPI-Key": key},
        params={
            "function": "TIME_SERIES_DAILY",
            "symbol": api_symbol,
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
    """Fetch from RapidAPI and merge into the existing file (or write fresh).

    The overwrite of the last 3 days replaces any Chaikin ``provisional`` placeholder with
    settled real-volume OHLCV, so volume-confirmation consumers (MFI, RBR) see real bars.
    """
    raw = _fetch_raw(symbol, outputsize)
    new_ts = raw["Time Series (Daily)"]

    if outputsize == "full" or not os.path.exists(path):
        _write_atomic(path, raw)
        return

    existing = _load_cache(path)
    if existing is None:
        _write_atomic(path, raw)
        return

    existing_ts = existing["Time Series (Daily)"]

    # Always overwrite the last 3 days of bars with fresh API data, so any Chaikin
    # placeholder (provisional) print is replaced by the official settled close.
    new_dates = sorted(new_ts.keys(), reverse=True)
    for d in new_dates[:3]:
        existing_ts[d] = new_ts[d]

    # Plus, append any older historical dates that are missing
    added = {d: v for d, v in new_ts.items() if d not in existing_ts}
    existing_ts.update(added)

    latest = max(existing_ts.keys())
    existing.setdefault("Meta Data", {})["3. Last Refreshed"] = latest
    _write_atomic(path, existing)


# ── Public API ────────────────────────────────────────────────────────────────

def repair_missing(symbols: list[str], today_str: str, force: bool = False) -> dict:
    """
    Scan Symbol_full/ and repair symbols with missing, corrupted, or stale files.
    Uses compact fetch (last 100 days) when file exists but has a gap; full fetch
    when file is absent or corrupt. Skips symbols that already have today's close
    (written by powergauge._append_ohlcv_entry).

    force=True fetches every listed symbol regardless of its gap (for re-testing a
    single symbol whose file isn't stale enough to trip MAX_GAP_DAYS).

    Rate: 14 s sleep between API calls → ≤5 req/min.
    """
    results = {"updated": 0, "skipped": 0, "errors": []}

    for i, sym in enumerate(symbols, 1):
        if sym.upper() in _UNSUPPORTED_SYMBOLS:
            results["skipped"] += 1
            continue

        path = os.path.join(OHLCV_DIR, f"{sym}_daily.json")
        needs, existing = _check_recovery(path, today_str)
        if not needs and not force:
            results["skipped"] += 1
            continue

        outputsize = "full" if existing is None else "compact"
        try:
            _fetch_and_merge(sym, path, outputsize=outputsize)
            results["updated"] += 1
            _log.console("  [RapidAPI] %s: %s fetch OK (%d/%d, updated=%d)",
                         sym, outputsize, i, len(symbols), results["updated"])
            time.sleep(SLEEP_SEC)
        except Exception as e:
            results["errors"].append((sym, str(e)))
            _log.error("  [RapidAPI] %s: ERROR - %s", sym, e)

    return results


# ── Legacy helpers (kept for compatibility) ───────────────────────────────────

def get_data(symbol: str, outputsize: str = "compact") -> str:
    """Low-level fetch — returns raw response text."""
    key = CFG.rapidapi_key
    if not key:
        raise RuntimeError("RapidAPI key not configured — set RAPIDAPI_KEY or config.json rapidapi.api_key")
    resp = requests.get(
        _BASE_URL,
        headers={**_HEADERS, "X-RapidAPI-Key": key},
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
    result = []
    if time_frame == 'D1':
        path = os.path.join(OHLCV_DIR, f"{symbol}_daily.json")
        with open(path) as f:
            data = json.load(f).get('Time Series (Daily)', {})
        cutoff = f"{year}-{month:02d}-{day:02d}"
        for date_key, value in data.items():
            if date_key < cutoff:
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

    args = [a for a in sys.argv[1:] if a != "--force"]
    force = "--force" in sys.argv[1:]

    if args:
        # Explicit symbols passed on command line
        syms = [s.upper() for s in args]
    else:
        # Load all symbols from Research sheet
        syms = load_symbols()

    _log.console("[RapidAPI] Recovery pass — %d symbols, today=%s, force=%s",
                 len(syms), today_str, force)
    result = repair_missing(syms, today_str, force=force)
    _log.console("[RapidAPI] Done: %d fetched, %d already current, %d errors",
                 result["updated"], result["skipped"], len(result["errors"]))
    for sym, err in result["errors"]:
        _log.error("  ERROR %s: %s", sym, err)
