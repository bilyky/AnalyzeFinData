"""Next earnings dates for the screen: an advisory flag on recommendations, never a gate.

PROD 2026-11-18/19: SA was bought and recommended on 11-18 and fell ~30% on 11-19. Nothing in the
cache said when a company reports, so a recommendation could not warn. Two sources carry a date,
each only for some symbols (checked 2026-11-19):

* E*TRADE quotes with ``require_earnings_date``: ``All.nextEarningDate`` ("MM/DD/YYYY"). Empty
  for many names, and sometimes a date that has already passed (AAPL on 11-19: 11/02/2026).
* Chaikin's raw response (Data/Symbol_raw): ``epsData.nextReportDate`` ("Thu Dec 17", no year);
  84 of 533 symbols had one on 11-17.

``build`` takes the earliest date on or after today from either source and saves
Data/earnings_calendar.json; ``note`` turns it into the text a recommendation shows. Neither SA
source had a date, so this would not have flagged SA; it covers the cases where a date exists.
The E*TRADE read uses the data-plane client, which cannot renew a token or open a browser.
"""
from __future__ import annotations

import datetime
import gzip
import json
import os

from aether import etrade, paths
from aether.logger import get_logger as _get_logger

_log = _get_logger("earnings_calendar")

FILE_NAME = "earnings_calendar.json"
WARN_DAYS = 7          # a report this many days ahead or sooner is shown on a recommendation
MAX_AGE_DAYS = 4       # an older calendar (a long weekend plus a missed run) is not shown
_BATCH = 25            # E*TRADE quote limit per request


def _path() -> str:
    return os.path.join(paths.data_dir(), FILE_NAME)


def parse_etrade(text) -> datetime.date | None:
    try:
        return datetime.datetime.strptime(str(text).strip(), "%m/%d/%Y").date()
    except ValueError:
        return None


def parse_chaikin(text, today: datetime.date) -> datetime.date | None:
    """'Thu Dec 17' -> the date in today's year, or next year's when that is over a month past
    (a December snapshot naming a January report)."""
    try:
        md = datetime.datetime.strptime(str(text).strip(), "%a %b %d")
    except ValueError:
        return None
    for year in (today.year, today.year + 1):
        try:
            d = datetime.date(year, md.month, md.day)
        except ValueError:          # Feb 29 in a non-leap year
            continue
        if d >= today - datetime.timedelta(days=31):
            return d
    return None


def chaikin_next(symbol: str, day: str, today: datetime.date) -> datetime.date | None:
    path = os.path.join(paths.symbol_raw_dir(), symbol, f"{symbol}_{day}.json.gz")
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            eps = ((json.load(f) or {}).get("data") or {}).get("epsData") or {}
    except (OSError, ValueError):
        return None
    return parse_chaikin(eps.get("nextReportDate"), today) if eps.get("nextReportDate") else None


def etrade_next(symbols: list) -> dict:
    """{SYMBOL: date} from E*TRADE quotes. A failed batch is logged and skipped."""
    out = {}
    try:
        market = etrade.ETradeClient("production", role="data").market.raw_market()
    except Exception as e:
        _log.warning(f"Earnings calendar: no E*TRADE data client ({type(e).__name__}: {e}); Chaikin dates only.")
        return out
    for i in range(0, len(symbols), _BATCH):
        batch = symbols[i:i + _BATCH]
        try:
            data = market.get_quote(batch, detail_flag="all", require_earnings_date=True, resp_format="json")
        except Exception as e:
            _log.warning(f"Earnings calendar: E*TRADE quotes failed for {len(batch)} symbols: {type(e).__name__}")
            continue
        for q in ((data or {}).get("QuoteResponse") or {}).get("QuoteData") or []:
            sym = ((q.get("Product") or {}).get("symbol") or "").upper()
            d = parse_etrade((q.get("All") or {}).get("nextEarningDate"))
            if sym and d:
                out[sym] = d
    return out


def build(symbols: list, today: datetime.date | None = None, fetch_etrade=etrade_next) -> dict:
    """Merge both sources for ``symbols`` and save the calendar. Returns it."""
    today = today or datetime.date.today()
    day = today.isoformat()
    syms = sorted({s.upper() for s in symbols if s})
    et = fetch_etrade(syms)
    out = {}
    for s in syms:
        e, c = et.get(s), chaikin_next(s, day, today)
        future = [d for d in (e, c) if d and d >= today]
        out[s] = {"next": min(future).isoformat() if future else None,
                  "etrade": e.isoformat() if e else None, "chaikin": c.isoformat() if c else None}
    cal = {"built": day, "symbols": out}
    tmp = _path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cal, f, indent=1)
    os.replace(tmp, _path())
    known = sum(1 for v in out.values() if v["next"])
    _log.info(f"Earnings calendar: {known} of {len(out)} symbols have a next report date "
              f"(E*TRADE {sum(1 for v in out.values() if v['etrade'])}, "
              f"Chaikin {sum(1 for v in out.values() if v['chaikin'])}).")
    return cal


def load() -> dict:
    try:
        with open(_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def note(symbol: str, today: datetime.date | None = None, cal: dict | None = None,
         unknown: bool = False) -> str:
    """'earnings in 3 days (2026-11-24)' when a report is due within WARN_DAYS; with ``unknown``,
    'earnings date unknown' when neither source has one. "" otherwise, or when the calendar is
    missing or more than MAX_AGE_DAYS old."""
    today = today or datetime.date.today()
    cal = load() if cal is None else cal
    try:
        built = datetime.date.fromisoformat(cal.get("built", ""))
    except ValueError:
        return ""
    if (today - built).days > MAX_AGE_DAYS:
        return ""
    entry = (cal.get("symbols") or {}).get((symbol or "").upper())
    if entry is None:
        return ""
    if not entry.get("next"):
        return "earnings date unknown" if unknown else ""
    days = (datetime.date.fromisoformat(entry["next"]) - today).days
    if 0 <= days <= WARN_DAYS:
        when = "today" if days == 0 else ("tomorrow" if days == 1 else f"in {days} days")
        return f"earnings {when} ({entry['next']})"
    return ""
