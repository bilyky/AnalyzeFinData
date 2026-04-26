"""
Populate Data/Symbol cache for the last N trading days.

For each trading day that is missing cache files, fetches live data from the
Chaikin Analytics API and saves JSON under that date.  Days already fully
cached are skipped entirely.  Run this once to backfill, then run daily.

Usage:
    python run_history.py            # last 14 trading days (default)
    python run_history.py 10         # last 10 trading days

Proxy:
    Inside Intel network  — proxy is auto-detected from environment or defaults
                             to proxy-dmz.intel.com:912
    Outside Intel network — set HTTP_PROXY= (empty) before running:
        Windows:  set HTTP_PROXY=  &&  set HTTPS_PROXY=
        macOS/Linux: unset HTTP_PROXY; unset HTTPS_PROXY

Python deps: requests, openpyxl, playwright (for browser login fallback)
"""

import datetime
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import powergauge

US_HOLIDAYS_2026 = {
    datetime.date(2026, 1, 1),   # New Year's Day
    datetime.date(2026, 1, 19),  # MLK Day
    datetime.date(2026, 2, 16),  # Presidents' Day
    datetime.date(2026, 4, 3),   # Good Friday
    datetime.date(2026, 5, 25),  # Memorial Day
    datetime.date(2026, 7, 3),   # Independence Day (observed)
    datetime.date(2026, 9, 7),   # Labor Day
    datetime.date(2026, 11, 26), # Thanksgiving
    datetime.date(2026, 11, 27), # Day after Thanksgiving
    datetime.date(2026, 12, 25), # Christmas
}


def trading_days(n: int) -> list[datetime.date]:
    days = []
    d = datetime.date.today()
    while len(days) < n:
        if d.weekday() < 5 and d not in US_HOLIDAYS_2026:
            days.append(d)
        d -= datetime.timedelta(days=1)
    return days  # most recent first


def load_symbols() -> list[str]:
    import openpyxl
    wb = openpyxl.load_workbook(powergauge.XLSX_FILE, data_only=True)
    ws = wb['Research']
    syms = []
    for row in ws.iter_rows(min_row=2):
        val = row[3].value
        if val and str(val).strip():
            syms.append(str(val).strip())
    return syms


def days_missing(symbols: list[str], day: datetime.date) -> list[str]:
    symbol_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Data", "Symbol")
    return [s for s in symbols
            if not os.path.exists(os.path.join(symbol_dir, f"{s}_{day}.json"))]


def main():
    n_days = int(sys.argv[1]) if len(sys.argv) > 1 else 14

    print(f"Loading symbols from Research sheet...")
    symbols = load_symbols()
    print(f"  {len(symbols)} symbols")

    days = trading_days(n_days)
    print(f"Trading days to check: {days[-1]} -> {days[0]} ({len(days)} days)\n")

    session_id = powergauge.login()
    print(f"Session: {session_id[:8]}...\n")

    for day in reversed(days):   # oldest first so prevPG chain builds forward
        missing = days_missing(symbols, day)
        if not missing:
            print(f"{day}: all {len(symbols)} symbols cached — skip")
            continue

        print(f"{day}: fetching {len(missing)}/{len(symbols)} symbols...")
        ok = 0
        skip = 0
        for symbol in missing:
            try:
                pg = powergauge.get_symbol_data(
                    symbol, day, from_cache=True, session_id=session_id
                )
                if pg.price == -1:
                    skip += 1
                else:
                    ok += 1
            except EnvironmentError:
                print("  Session expired — re-logging in...")
                session_id = powergauge.login()
                pg = powergauge.get_symbol_data(
                    symbol, day, from_cache=True, session_id=session_id
                )
                ok += 1
            except Exception as e:
                print(f"  {symbol}: ERROR {e}")

        print(f"  done: {ok} fetched, {skip} no-data, {len(missing)-ok-skip} errors\n")

    print("History backfill complete.")
    print("Run check_from_xls to update the Research sheet with today's data.")


if __name__ == "__main__":
    main()
