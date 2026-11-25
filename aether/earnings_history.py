"""Past earnings report dates from SEC filings (R&D #98 data step, order R2, owner 2026-11-24).

A US company files an 8-K with item 2.02 ("Results of Operations and Financial Condition") when it
releases quarterly results. EDGAR's submissions data lists every filing with its form, items and
acceptance time, back to the early 2000s, so this gives a dated history of reports for the US
filers in our universe. Foreign issuers (6-K / 20-F), ETFs and funds have none.

Each event carries:
  filed          the filing date
  accepted_et    the acceptance time in New York time (EDGAR gives it in UTC)
  first_session  the first trading session that opens after the release: the filing day when it
                 was accepted before 09:30 ET, otherwise the next session. This is the day the
                 market first trades on the news (the reaction day for a gap study).

Why: the 10-22 missed winners made 34-97% of their 10-day moves on one day each, all between
10-27 and 11-05 (earnings season), and SA's 30% drop on 11-19 came with no report date in our data.
The studies that use this (a pre-report risk rule, post-earnings drift, R&D #102) are separate,
each pre-registered.

`build` is the only writer of Data/earnings_history.json. SEC access goes through
aether.ai_buildout's EDGAR client (file cache, fair-access pacing, stop after repeated 403s).

    python -m aether.earnings_history [--symbols A,B] [--status]
"""
from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
import sys

import pytz

from aether import ai_buildout, paths
from aether.logger import get_logger as _get_logger

_log = _get_logger("earnings_history")

FILE_NAME = "earnings_history.json"
FORMS = ("8-K",)                 # amendments (8-K/A) restate a release; they are not a new report
ITEM = "2.02"
_ET = pytz.timezone("America/New_York")
_OPEN = datetime.time(9, 30)


def history_path() -> str:
    return os.path.join(paths.data_dir(), FILE_NAME)


def accepted_et(stamp: str) -> datetime.datetime | None:
    """EDGAR's acceptanceDateTime ("2026-10-29T11:02:04.000Z", UTC) in New York time."""
    try:
        t = datetime.datetime.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc)
    except (TypeError, ValueError):
        return None
    return t.astimezone(_ET)


def first_session(accepted: datetime.datetime, sessions: list) -> str | None:
    """The first session in `sessions` (sorted ISO dates) that opens after the release."""
    day = accepted.date().isoformat()
    for s in sessions:
        if s > day or (s == day and accepted.time() < _OPEN):
            return s
    return None


def events_from_pages(pages: list, sessions: list) -> list:
    """Earnings events from submissions pages (each a dict of parallel lists: form, items,
    filingDate, acceptanceDateTime), oldest first, one per accession."""
    out, seen = [], set()
    for p in pages:
        forms = p.get("form") or []
        for i, form in enumerate(forms):
            items = (p.get("items") or [""] * len(forms))[i] or ""
            if form not in FORMS or ITEM not in [x.strip() for x in items.split(",")]:
                continue
            acc = (p.get("accessionNumber") or [None] * len(forms))[i]
            if acc in seen:
                continue
            seen.add(acc)
            at = accepted_et((p.get("acceptanceDateTime") or [""] * len(forms))[i])
            filed = (p.get("filingDate") or [""] * len(forms))[i]
            primary = (p.get("primaryDocument") or [None] * len(forms))[i]
            out.append({"filed": filed, "accepted_et": at.isoformat(timespec="seconds") if at else None,
                        "first_session": first_session(at, sessions) if at else None,
                        "accession": acc, "primary_doc": primary})
    return sorted(out, key=lambda e: (e["filed"], e["accepted_et"] or ""))


def pages_for(cik: int, recent_ttl_hours: float = 24 * 7) -> tuple:
    """(pages, stale, missing): the recent page plus every older page for one company; stale when
    any page came from an expired cache because SEC could not be reached; missing = older pages
    that could not be read at all. pages is None when EDGAR has nothing for the company."""
    sub = ai_buildout.submissions(cik, ttl_hours=recent_ttl_hours)
    if not sub:
        return None, False, 0
    names = [f"sub_{cik}.json"]
    filings = sub.get("filings") or {}
    pages, missing = [filings.get("recent") or {}], 0
    for f in filings.get("files") or []:
        page = ai_buildout.submissions_file(f["name"])
        names.append(f"subfile_{f['name']}")
        if page:
            pages.append(page)
        else:
            missing += 1
    stale = bool(set(names) & ai_buildout.stale_served())
    return pages, stale, missing


def trading_sessions() -> list:
    """SPY's trading days from the OHLCV cache (the session calendar)."""
    with open(os.path.join(paths.ohlcv_dir(), "SPY_daily.json"), encoding="utf-8") as f:
        return sorted(json.load(f)["Time Series (Daily)"])


def universe() -> list:
    return sorted(os.path.basename(p)[:-len("_daily.json")] for p in glob.glob(os.path.join(paths.ohlcv_dir(), "*_daily.json")))


def build(symbols: list | None = None, path: str | None = None, recent_ttl_hours: float = 24 * 7) -> dict:
    """Fetch (through the EDGAR cache) and write the history. Symbols without a SEC CIK or without
    any 8-K 2.02 are listed, not dropped silently."""
    symbols = symbols or universe()
    sessions = trading_sessions()
    ciks = ai_buildout.ticker_cik_map()
    out = {"built": datetime.datetime.now().isoformat(timespec="seconds"), "source": "SEC EDGAR 8-K item 2.02",
           "sessions_to": sessions[-1], "symbols": {}, "no_cik": [], "no_reports": [], "fetch_failed": [],
           "stale": [], "incomplete": [], "sec_blocked": False}
    for n, sym in enumerate(symbols, 1):
        cik = ciks.get(sym.upper().replace(".", "-")) or ciks.get(sym.upper())
        if not cik:
            out["no_cik"].append(sym)
            continue
        pages, stale, missing = pages_for(cik, recent_ttl_hours)
        if pages is None:
            out["fetch_failed"].append(sym)
            continue
        if stale:
            out["stale"].append(sym)
        if missing:
            out["incomplete"].append(sym)
        ev = events_from_pages(pages, sessions)
        if ev:
            out["symbols"][sym] = {"cik": cik, "events": ev}
        else:
            out["no_reports"].append(sym)
        if n % 100 == 0:
            _log.info(f"earnings history: {n}/{len(symbols)} symbols")
    out["sec_blocked"] = ai_buildout.sec_blocked()
    if out["sec_blocked"] or out["stale"] or out["incomplete"]:
        _log.error(f"earnings history is NOT complete: SEC blocked={out['sec_blocked']}, "
                   f"{len(out['stale'])} symbols from an expired cache, {len(out['incomplete'])} missing older pages")
    target = path or history_path()
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    os.replace(tmp, target)
    return out


def load(path: str | None = None) -> dict:
    try:
        with open(path or history_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def status(data: dict) -> dict:
    syms = data.get("symbols") or {}
    firsts = [e["filed"] for v in syms.values() for e in v["events"][:1]]
    return {"built": data.get("built"), "symbols_with_reports": len(syms),
            "events": sum(len(v["events"]) for v in syms.values()),
            "earliest": min(firsts) if firsts else None, "no_cik": len(data.get("no_cik") or []),
            "no_reports": len(data.get("no_reports") or []), "fetch_failed": len(data.get("fetch_failed") or []),
            "stale": len(data.get("stale") or []), "incomplete": len(data.get("incomplete") or []),
            "sec_blocked": bool(data.get("sec_blocked")),
            "complete": not (data.get("sec_blocked") or data.get("stale") or data.get("incomplete"))}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--symbols", default=None, help="comma-separated (default: every symbol with OHLCV)")
    ap.add_argument("--status", action="store_true", help="summarize the saved history; no network")
    a = ap.parse_args(argv)
    if a.status:
        sys.stdout.write(json.dumps(status(load()), indent=1) + "\n")
        return 0
    data = build([s.strip().upper() for s in a.symbols.split(",")] if a.symbols else None)
    st = status(data)
    sys.stdout.write(json.dumps(st, indent=1) + "\n")
    return 0 if st["complete"] else 1


if __name__ == "__main__":
    sys.exit(main())
