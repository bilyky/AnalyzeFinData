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

import argparse
import datetime
import json
import os
import sys
import time

import numpy as np
import requests

from aether import paths, trash
from aether.batch_failures import most_failed
from aether.logger import get_logger as _get_logger
from bar_provenance import is_provisional, real_dates
from config import CFG
from run_history import load_symbols

_log = _get_logger("rapidapi")

_DIR      = os.path.dirname(os.path.abspath(__file__))
OHLCV_DIR = paths.ohlcv_dir()   # $AETHER_CACHE_DIR/Symbol_full, else <checkout>/Data/Symbol_full
MAX_GAP_DAYS = 30   # trigger compact/full fetch if latest entry is this many calendar days behind
SLEEP_SEC    = 14   # 14 s between requests → 4.3 req/min (safe under 5/min limit)
COMPACT_WINDOW_DAYS = 120  # a compact fetch returns ~100 sessions (~140 calendar days)
QUOTA_STOP_AFTER = 3  # consecutive 429s = quota spent: stop the run instead of burning 14 s per symbol
# Alpha Vantage's "Burst pattern detected" (3-4 of ~400 fetches per PROD run, 11-02/11-03,
# despite the 14 s spacing) clears after a pause: retry that symbol once. Capped per pass so
# the retries stay inside pass_timeout_seconds' 600 s slack.
BURST_RETRY_WAIT_S = 60
BURST_RETRIES_PER_PASS = 5
DORMANT_DAYS = 45     # no real bar this long = likely delisted/dead: queue it LAST, not first
REQUEST_TIMEOUT = 30  # per-call HTTP timeout (s)


def pass_timeout_seconds(max_fetches: int | None = None) -> int:
    """Wall-clock budget a caller must allow one recovery pass: every budgeted fetch at its
    worst case (sleep + full HTTP timeout) plus slack for the pre-scan. A caller that kills
    the pass sooner silently caps it — PROD's daily_task killed it at 600 s (~42 fetches)."""
    budget = CFG.rapidapi_max_fetches if max_fetches is None else max_fetches
    return budget * (SLEEP_SEC + REQUEST_TIMEOUT) + 600

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


def _last_real_date(cache: dict | None) -> str:
    """Newest non-placeholder bar date, or "" (sorts first) when there is none/no file."""
    if not cache:
        return ""
    real = real_dates(cache["Time Series (Daily)"])
    return real[-1] if real else ""


def _queue_rank(existing: dict | None, today_str: str, path: str | None = None) -> tuple:
    """Repair priority (lower = sooner): a missing file first (a new symbol needs its full
    fetch), then active symbols oldest-real-bar first, and dormant ones last — no real bar
    for DORMANT_DAYS, or a file holding an API error instead of a series (e.g. Alpha
    Vantage "Invalid API call" for a delisted ticker) — so they cannot eat the budget
    every run."""
    if existing is None:
        return (2, "") if path and os.path.exists(path) else (0, "")
    last = _last_real_date(existing)
    cutoff = (datetime.date.fromisoformat(today_str)
              - datetime.timedelta(days=DORMANT_DAYS)).isoformat()
    return (2, last) if last < cutoff else (1, last)


def _is_quota_error(err: Exception) -> bool:
    resp = getattr(err, "response", None)
    return getattr(resp, "status_code", None) == 429


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
    # A placeholder stranded inside the compact window (a missed nightly pass, a weekend or
    # holiday print) is repairable by the same single compact fetch.
    cutoff = (datetime.date.fromisoformat(today_str)
              - datetime.timedelta(days=COMPACT_WINDOW_DAYS)).isoformat()
    if any(is_provisional(ts[d]) for d in ts if d >= cutoff):
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
        timeout=REQUEST_TIMEOUT,
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

    Every Chaikin ``provisional`` placeholder inside the response window is settled —
    replaced by the real bar, or dropped if the API has no session that day — so range and
    volume consumers (ATR, MFI, RBR) see real bars.
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

    # Settle EVERY placeholder the response covers, not just the newest 3: replace it with
    # the real bar, or drop it when its date lies inside the response window yet the API
    # has no bar for it (weekend / holiday — never a session). Placeholders older than the
    # window are left for a later full fetch.
    lo, hi = min(new_ts), max(new_ts)
    for d in [d for d, bar in existing_ts.items() if is_provisional(bar)]:
        if d in new_ts:
            existing_ts[d] = new_ts[d]
        elif lo <= d <= hi:
            del existing_ts[d]

    # Plus, append any older historical dates that are missing
    added = {d: v for d, v in new_ts.items() if d not in existing_ts}
    existing_ts.update(added)

    latest = max(existing_ts.keys())
    existing.setdefault("Meta Data", {})["3. Last Refreshed"] = latest
    _write_atomic(path, existing)


# ── Public API ────────────────────────────────────────────────────────────────

def held_symbols(game_path: str | None = None) -> set[str]:
    """Symbols the paper game holds (Data/ai_portfolio_game.json), read-only; empty on any error.
    Their stops and the pricing checks read these bars, so the recovery pass repairs them first."""
    game_path = game_path or os.path.join(paths.data_dir(), "ai_portfolio_game.json")
    try:
        with open(game_path, encoding="utf-8") as f:
            return {s.upper() for s in (json.load(f).get("positions") or {})}
    except (OSError, ValueError, AttributeError):
        return set()


def run_symbols(explicit: list[str], held: set[str], sheet=None) -> list[str]:
    """The CLI's symbol list: the explicit symbols if given, else the Research sheet plus any
    held position missing from it (a held name off the sheet still needs bars)."""
    if explicit:
        return [s.upper() for s in explicit]
    syms = list((sheet or load_symbols)())
    return syms + sorted(held - {s.upper() for s in syms})


def repair_missing(symbols: list[str], today_str: str, force: bool = False,
                   max_fetches: int | None = None, priority: set[str] | None = None) -> dict:
    """
    Scan Symbol_full/ and repair symbols with missing, corrupted, or stale files.
    Uses compact fetch (last 100 days) when file exists but has a gap; full fetch
    when file is absent or corrupt. Skips symbols that already have today's close
    (written by powergauge._append_ohlcv_entry).

    force=True fetches every listed symbol regardless of its gap (for re-testing a
    single symbol whose file isn't stale enough to trip MAX_GAP_DAYS).

    Rate: 14 s sleep between API calls → ≤5 req/min.

    Budget: at most max_fetches (default CFG.rapidapi_max_fetches) API calls per run, spent
    MOST-STARVED FIRST — symbols needing repair are ordered by their newest real bar,
    oldest first (missing files before them, dormant/delisted names after; _queue_rank).
    A fixed order starved the tail of the list every night once the quota ran out (the
    same ~23 symbols 429'd daily and were never repaired); now whatever a run misses is
    first in line for the next. QUOTA_STOP_AFTER consecutive 429s end the run.
    Symbols left for the next run are counted in results["deferred"].

    priority: symbols repaired before everything else (the CLI passes the held positions:
    on PROD a held name's placeholder bar waited a week behind ~150 deferred symbols).
    """
    results = {"updated": 0, "skipped": 0, "errors": [], "deferred": 0, "quota_stopped": False,
               "failed_priority": []}
    budget = CFG.rapidapi_max_fetches if max_fetches is None else max_fetches

    # Cross-process file lock to prevent multiple processes from running recovery simultaneously
    lock_path = os.path.join(os.path.dirname(OHLCV_DIR), "rapidapi.lock")
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    fd = None
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            # A lock older than the longest legitimate pass is from a crashed/killed run.
            if time.time() - os.path.getmtime(lock_path) > pass_timeout_seconds(budget):
                trash.soft_delete(lock_path, reason="rapidapi-lock-stale", force=True)
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except OSError:
            pass

    if fd is None:
        # A pass killed by its caller's timeout never reaches the finally that deletes the
        # lock, so say how old it is and when it expires — a manual run inside that window
        # must not look like a silent skip.
        try:
            age = time.time() - os.path.getmtime(lock_path)
        except OSError:
            age = 0.0
        _log.warning("  [RapidAPI] Recovery lock held (%s, %.0f min old) — another pass is running, or a "
                     "killed pass left it; it is treated as stale after %.0f min. Skipping to "
                     "prevent rate-limit collisions.", lock_path, age / 60, pass_timeout_seconds(budget) / 60)
        # ``locked`` distinguishes "another process owns the lock" from "these symbols were
        # already current" — both otherwise look like updated=0. Callers (e.g. the on-demand
        # self-healer) use it to retry later instead of treating the symbol as un-healable.
        # Same keys as a normal run (the CLI summary reads "deferred"; it raised KeyError here).
        return {**results, "skipped": len(symbols), "locked": True}

    try:
        queue = []
        for order, sym in enumerate(symbols):
            if sym.upper() in _UNSUPPORTED_SYMBOLS:
                results["skipped"] += 1
                continue
            path = os.path.join(OHLCV_DIR, f"{sym}_daily.json")
            needs, existing = _check_recovery(path, today_str)
            if not needs and not force:
                results["skipped"] += 1
                continue
            rank = (-1, "") if priority and sym.upper() in priority else _queue_rank(existing, today_str, path)
            queue.append((rank, order, sym, path, existing))
        queue.sort(key=lambda q: (q[0], q[1]))          # most-starved first, stable
        results["deferred"] = max(0, len(queue) - budget)
        _log.info("  [RapidAPI] %d need repair, budget %d this run, %d deferred to the next run",
                     len(queue), budget, results["deferred"])

        quota_hits = 0
        burst_retries = 0
        for i, (_last, _order, sym, path, existing) in enumerate(queue[:budget], 1):
            outputsize = "full" if existing is None else "compact"
            try:
                try:
                    _fetch_and_merge(sym, path, outputsize=outputsize)
                except RuntimeError as e:
                    if "Burst pattern" not in str(e) or burst_retries >= BURST_RETRIES_PER_PASS:
                        raise
                    burst_retries += 1
                    _log.info("  [RapidAPI] %s: burst limit hit; retrying once in %d s", sym, BURST_RETRY_WAIT_S)
                    time.sleep(BURST_RETRY_WAIT_S)
                    _fetch_and_merge(sym, path, outputsize=outputsize)
                results["updated"] += 1
                quota_hits = 0
                _log.console("  [RapidAPI] %s: %s fetch OK (%d/%d, updated=%d)",
                             sym, outputsize, i, min(len(queue), budget), results["updated"])
                time.sleep(SLEEP_SEC)
            except Exception as e:
                results["errors"].append((sym, str(e)))
                if priority and sym.upper() in priority:
                    results["failed_priority"].append(sym)
                # WARNING: the next pass retries it (most-starved first). log_run_summary raises
                # the run to ERROR (the recovery email) only when it matters.
                _log.warning("  [RapidAPI] %s: fetch failed - %s", sym, e)
                quota_hits = quota_hits + 1 if _is_quota_error(e) else 0
                if quota_hits >= QUOTA_STOP_AFTER:
                    left = min(len(queue), budget) - i
                    results["deferred"] += left
                    results["quota_stopped"] = True
                    _log.warning("  [RapidAPI] %d consecutive 429s — quota spent; stopping, "
                                 "%d more deferred to the next run", quota_hits, left)
                    break
                # Sleep even on failure to avoid a rapid-fire cascade hammering the API
                time.sleep(SLEEP_SEC)
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
            trash.soft_delete(lock_path, reason="rapidapi-lock", force=True)

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
        timeout=REQUEST_TIMEOUT,
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


def run_needs_attention(result: dict) -> bool:
    """True when the pass's failures need a person: a held position failed, the quota stopped
    the pass, or more than half of the fetches it attempted failed. A few symbols failing
    (a timeout, Alpha Vantage's burst guard) is retried by the next pass and is not an alert."""
    failed = len(result.get("errors") or [])
    return bool(result.get("failed_priority") or result.get("quota_stopped")
                or most_failed(failed, result.get("updated", 0) + failed))


def log_run_summary(result: dict) -> None:
    """One 'Done' line, plus ONE line naming the failed symbols: ERROR (the watchdog's recovery
    email) only when run_needs_attention, else WARNING. Each failure was logged when it
    happened, so this does not repeat the errors."""
    _log.info("[RapidAPI] Done: %d fetched, %d already current, %d errors, %d deferred%s",
              result["updated"], result["skipped"], len(result["errors"]), result["deferred"],
              " (stopped: quota spent)" if result["quota_stopped"] else "")
    if not result["errors"]:
        return
    held = result.get("failed_priority") or []
    msg = ("  [RapidAPI] %d symbol(s) failed this run (retried next run): %s%s"
           % (len(result["errors"]), ", ".join(sym for sym, _ in result["errors"]),
              f"; held positions among them: {', '.join(held)}" if held else ""))
    (_log.error if run_needs_attention(result) else _log.warning)(msg)


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    today_str = str(datetime.date.today())

    ap = argparse.ArgumentParser(description="RapidAPI OHLCV recovery pass")
    ap.add_argument("symbols", nargs="*", help="symbols to repair (default: the Research sheet)")
    ap.add_argument("--force", action="store_true", help="fetch even symbols that look current")
    ap.add_argument("--max-fetches", type=int, default=None,
                    help=f"API calls allowed this run (default {CFG.rapidapi_max_fetches})")
    cli = ap.parse_args()
    force, max_fetches = cli.force, cli.max_fetches

    held = held_symbols()
    syms = run_symbols(cli.symbols, held)
    _log.console("[RapidAPI] Recovery pass — %d symbols (%d held), today=%s, force=%s",
                 len(syms), len(held), today_str, force)
    result = repair_missing(syms, today_str, force=force, max_fetches=max_fetches, priority=held)
    log_run_summary(result)
