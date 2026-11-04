"""
Populate Data/Symbol cache for the last N trading days.

For the most recent trading day (today, or the last session when run on a weekend or
holiday) it fetches the symbols that are missing and saves them under that date. Days
already fully cached are skipped.

Older missing days are listed but NOT fetched: the Chaikin API only serves current values
(it ignores date parameters), so a fetch would store today's data under an old date. For
PGR history use scripts/utils/chaikin_chart_backfill.py (daily PGR back to 2021-11).

Usage:
    python run_history.py            # last 14 trading days (default)
    python run_history.py 10         # last 10 trading days

Proxy:
    Behind a corporate proxy — proxy is auto-detected from the HTTP_PROXY /
                               HTTPS_PROXY environment variables.
    Direct connection       — clear the proxy env vars before running:
        Windows:  set HTTP_PROXY=  &&  set HTTPS_PROXY=
        macOS/Linux: unset HTTP_PROXY; unset HTTPS_PROXY

Python deps: requests, openpyxl, playwright (for browser login fallback)
"""

import datetime
import os
import sys
import pytz
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import powergauge
from aether import market_calendar, paths
from aether.batch_failures import log_batch_failures
from aether.logger import get_logger

_log = get_logger("run_history")

def trading_days(n: int, today: datetime.date | None = None) -> list[datetime.date]:
    """The last n NYSE sessions up to today (aether.market_calendar), most recent first."""
    days = []
    d = today or datetime.date.today()
    while len(days) < n:
        if market_calendar.is_session(d):
            days.append(d)
        d -= datetime.timedelta(days=1)
    return days


def fetchable(day: datetime.date, today: datetime.date | None = None) -> bool:
    """Only the most recent session can be fetched: the API serves current values, which are
    that session's (after a weekend or holiday nothing has changed since its close)."""
    return day == market_calendar.last_session_on_or_before(today or datetime.date.today())


def load_symbols() -> list[str]:
    import openpyxl, re
    _sym_re = re.compile(r"^[A-Z0-9._\-]+$")
    try:
        wb = openpyxl.load_workbook(powergauge.XLSX_FILE, data_only=True, read_only=True)
    except Exception:
        # Fallback if read_only=True still fails, though unlikely
        wb = openpyxl.load_workbook(powergauge.XLSX_FILE, data_only=True)
    ws = wb['Research']
    syms = []
    for row in ws.iter_rows(min_row=2):
        val = str(row[3].value or '').strip()
        if val and _sym_re.match(val):
            syms.append(val)
    return syms


def days_missing(symbols: list[str], day: datetime.date) -> list[str]:
    symbol_dir = paths.symbol_dir()
    if day < datetime.date.today():
        return [s for s in symbols
                if not (os.path.exists(os.path.join(symbol_dir, s, f"{s}_{day}.json")) or
                        os.path.exists(os.path.join(symbol_dir, f"{s}_{day}.json")))]

    # For today: re-fetch if the file is missing or was written before NYSE 4:05 PM ET
    # (pre-market / intraday writes use the same filename and must be overwritten at EOD).
    try:
        eod_ts = datetime.datetime.combine(
            day, datetime.time(16, 5),
            tzinfo=pytz.timezone("America/New_York")
        ).timestamp()
    except Exception:
        return list(symbols)  # safe fallback: fetch everything

    missing = []
    for s in symbols:
        p1 = os.path.join(symbol_dir, s, f"{s}_{day}.json")
        p2 = os.path.join(symbol_dir, f"{s}_{day}.json")
        path = p1 if os.path.exists(p1) else (p2 if os.path.exists(p2) else None)
        if path is None or os.path.getmtime(path) < eod_ts:
            missing.append(s)
    return missing


def main():
    n_days = int(sys.argv[1]) if len(sys.argv) > 1 else 14

    _log.console("Loading symbols from Research sheet...")
    symbols = load_symbols()
    _log.console(f"  {len(symbols)} symbols")

    days = trading_days(n_days)
    _log.console(f"Trading days to check: {days[-1]} -> {days[0]} ({len(days)} days)\n")

    session_id = powergauge.login()
    sid_str = session_id.get('jsessionid', '') if isinstance(session_id, dict) else str(session_id)
    _log.console(f"Session: {sid_str[:8]}...\n")

    for day in reversed(days):   # oldest first so prevPG chain builds forward
        missing = days_missing(symbols, day)
        if not missing:
            _log.console(f"{day}: all {len(symbols)} symbols cached — skip")
            continue
        if not fetchable(day):
            _log.warning(f"{day}: {len(missing)} symbols have no snapshot. Not fetched: the API serves "
                         f"current values only, so a fetch would store today's data under {day}. "
                         f"First missing: {', '.join(missing[:10])}")
            continue

        total = len(missing)
        _log.console(f"{day}: fetching {total}/{len(symbols)} symbols in parallel...")
        ok = 0
        skip = 0
        errors = 0
        failed = []
        
        workers = int(os.environ.get("CHAIKIN_WORKERS", "10"))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_to_sym = {
                pool.submit(
                    powergauge.get_symbol_data,
                    symbol,
                    day,
                    day != datetime.date.today()
                ): symbol
                for symbol in missing
            }
            done = 0
            for future in as_completed(future_to_sym):
                symbol = future_to_sym[future]
                done += 1
                # Progress is now line-per-message (the logger appends a newline
                # and a level prefix), so throttle to avoid one line per symbol.
                if done % 25 == 0 or done == total:
                    _log.console(f"  [{done}/{total}] symbols processed")
                try:
                    pg = future.result()
                    if pg.price == -1:
                        skip += 1
                    else:
                        ok += 1
                except powergauge.ChaikinSessionUnavailable as e:
                    # No Chaikin session (re-auth failed or its breaker is open): every other
                    # symbol and day would fail the same way, so stop the backfill. Any other
                    # error, including OSErrors (network, a locked cache file), is per symbol.
                    pool.shutdown(wait=False, cancel_futures=True)
                    _log.error(f"  {symbol}: {e} — stopping the backfill ({done}/{total} on {day}).")
                    raise
                except Exception as e:
                    _log.warning(f"  {symbol}: fetch failed - {e}")
                    failed.append(symbol)
                    errors += 1

        _log.console(f"  done: {ok} fetched, {skip} no-data, {errors} errors")
        log_batch_failures(_log, f"[run_history] {day}", failed, total)

    _log.console("History backfill complete.")
    _log.console("Run check_from_xls to update the Research sheet with today's data.")


if __name__ == "__main__":
    main()
