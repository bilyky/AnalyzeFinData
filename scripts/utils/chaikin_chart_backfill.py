"""
chaikin_chart_backfill.py — pull Chaikin's daily chart history (5 years) for every symbol.

GET /api/v2/chart/{symbol}?timeFrame=5Y&chartType=daily returns one row per session. The
daily snapshot cache (Data/Symbol) starts 2023-08 and lost the PGR factors 2026-09-01..#232;
this history goes back to 2021-11 for the fields RapidAPI doesn't have:

  dailyPgr, weeklyPgr      the PGR rating, 7-level scale (1 = Very Bearish .. 7 = Very Bullish)
  cmf, relativeStrength    Chaikin money flow and relative strength as numbers
  cycle, chaikinTrend20001, upperBand, lowerBand   Chaikin's own indicators
  close                    kept only to cross-check against RapidAPI (Symbol_full)

Not kept: OHLCV (RapidAPI has it), ATR/EMA (computable), and the signal and EPS fields:
checked 2026-11-03 on INTC, every signal was 0 for 5 years while the snapshot cache had 17
signal days in the last year, and every EPS field was empty.

Checked against the cache on INTC (798 days): the chart's dailyPgr for D-1 equals the
snapshot's corrected PGR for D 82% of the time (7->5 level map). A good record, not exact.

The full response (every field, unchanged) is also kept gzipped in
Data/Symbol_chart/raw/<SYM>_<date>.json.gz, so nothing is lost to the field choice above.
Only the latest raw file per symbol is kept (each is ~69 KB; the 5-year window covers the
older ones).

Writes Data/Symbol_chart/<SYM>_chart.json ({"rows": {date: {...}}}, merged with earlier
runs) and Data/Symbol_chart/_report.json with a content check per symbol: row count, PGR
values in range, and the median close difference vs RapidAPI over the last 60 shared days.

One GET per symbol, 2 s apart by default (--delay) (the daily screen makes ~535 of these calls a day). Stops on
the first auth error (401/403) or 3 errors in a row. Symbols fetched today are skipped
unless --force, so a stopped run can be restarted.

Usage (repo root, on PROD):
    python scripts/utils/chaikin_chart_backfill.py --symbols INTC AAPL      # try a few first
    python scripts/utils/chaikin_chart_backfill.py                           # every symbol in Data/Symbol
    python scripts/utils/chaikin_chart_backfill.py --report                  # re-check saved files only
"""
import argparse
import datetime
import gzip
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import powergauge  # noqa: E402
from aether import paths  # noqa: E402

URL = "https://members-backend.chaikinanalytics.com/api/v2/chart/{symbol}?timeFrame=5Y&chartType=daily"
KEEP = ("dailyPgr", "weeklyPgr", "cmf", "relativeStrength", "cycle", "chaikinTrend20001", "upperBand",
        "lowerBand", "close")
SLEEP = 2.0                 # seconds between calls (--delay)
MAX_ERRORS = 3


def _out(msg: str = "") -> None:
    sys.stdout.write(msg + "\n")


def chart_dir() -> str:
    return os.path.join(paths.cache_dir(), "Symbol_chart")


class AuthError(Exception):
    pass


def slim(rows: list) -> dict:
    """{date: {kept fields}} from the API rows; rows without a date are dropped."""
    return {r["date"]: {k: r.get(k) for k in KEEP} for r in rows if isinstance(r, dict) and r.get("date")}


def merge(old: dict, new: dict) -> dict:
    """New rows win on the same date (Chaikin may restate); older dates are kept."""
    return {**old, **new}


def check(rows: dict, rapid_close: dict) -> dict:
    """Content check for one symbol's saved rows."""
    dates = sorted(rows)
    pgr = [rows[d].get("dailyPgr") for d in dates]
    rated = [p for p in pgr if p not in (None, 0)]
    numeric = [p for p in rated if isinstance(p, (int, float))]
    shared = [d for d in dates if d in rapid_close and rows[d].get("close")][-60:]
    diffs = [abs(rows[d]["close"] / rapid_close[d] - 1) for d in shared if rapid_close[d]]
    res = {"rows": len(dates), "first": dates[0] if dates else None, "last": dates[-1] if dates else None,
           "rated_days": len(rated), "pgr_out_of_range": len(rated) - len(numeric) + sum(1 for p in numeric if not 1 <= p <= 7),
           "close_shared": len(diffs), "close_median_diff": statistics.median(diffs) if diffs else None}
    problems = []
    if not dates:
        problems.append("no rows")
    if res["pgr_out_of_range"]:
        problems.append(f"{res['pgr_out_of_range']} PGR values outside 1-7 or not numbers")
    if res["close_median_diff"] is not None and res["close_median_diff"] > 0.01:
        problems.append(f"close differs from RapidAPI by {res['close_median_diff']:.1%} (median, last 60 days)")
    res["problems"] = problems
    return res


def rapid_closes(symbol: str) -> dict:
    p = os.path.join(paths.ohlcv_dir(), f"{symbol}_daily.json")
    if not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as f:
        ts = json.load(f).get("Time Series (Daily)", {})
    return {d: float(v["4. close"]) for d, v in ts.items() if not v.get("provisional")}


def _headers(session: dict) -> dict:
    return {"jsessionid": session.get("jsessionid", ""), "x-session-id": session.get("jsessionid", ""),
            "uuid": session.get("uuid") or powergauge._chaikin_uuid(), "jwttoken": session.get("jwttoken", ""),
            "x-api-key": powergauge._CHAIKIN_API_KEY, "x-app-id": "omni", "User-Agent": powergauge._CHAIKIN_UA,
            "accept": "application/json, text/plain, */*"}


def fetch(symbol: str, session: dict) -> dict:
    r = powergauge._get_http_session().get(URL.format(symbol=symbol), headers=_headers(session), timeout=(5, 60))
    if r.status_code in (401, 403):
        raise AuthError(f"HTTP {r.status_code}")
    r.raise_for_status()
    return r.json() or {}


def save_raw(symbol: str, payload) -> None:
    """Write today's raw response and drop this symbol's older raw files."""
    d = os.path.join(chart_dir(), "raw")
    os.makedirs(d, exist_ok=True)
    name = f"{symbol}_{datetime.date.today().isoformat()}.json.gz"
    with gzip.open(os.path.join(d, name), "wt", encoding="utf-8") as f:
        json.dump(payload, f)
    for old in os.listdir(d):
        if old != name and old.startswith(f"{symbol}_") and old[len(symbol) + 1:][:4].isdigit():
            os.remove(os.path.join(d, old))


def _load(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(symbol: str, rows: dict) -> None:
    p = os.path.join(chart_dir(), f"{symbol}_chart.json")
    old = _load(p).get("rows", {})
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"symbol": symbol, "fetched": datetime.date.today().isoformat(), "rows": merge(old, rows)}, f)
    os.replace(tmp, p)


def run(symbols: list, force=False, fetch_fn=None, sleep=SLEEP) -> dict:
    """Fetch and save each symbol; returns {"fetched", "skipped", "failed", "stopped"}."""
    os.makedirs(chart_dir(), exist_ok=True)
    today = datetime.date.today().isoformat()
    res = {"fetched": [], "skipped": [], "failed": [], "stopped": None}
    if fetch_fn is None:
        session = powergauge.ensure_valid_session()

        def fetch_fn(sym):
            return fetch(sym, session)
    errors = 0
    for i, s in enumerate(symbols):
        if not force and _load(os.path.join(chart_dir(), f"{s}_chart.json")).get("fetched") == today:
            res["skipped"].append(s)
            continue
        try:
            payload = fetch_fn(s)
            rows = slim(payload.get("data") or [] if isinstance(payload, dict) else payload)
        except AuthError as e:
            res["stopped"] = f"auth error on {s} ({e}): renew the Chaikin session, then re-run"
            break
        except Exception as e:  # one bad symbol shouldn't stop the run; three in a row do
            res["failed"].append(s)
            errors += 1
            _out(f"  {s}: {e}")
            if errors >= MAX_ERRORS:
                res["stopped"] = f"{MAX_ERRORS} errors in a row (last: {s})"
                break
            continue
        errors = 0
        if rows:
            save_raw(s, payload)
            save(s, rows)
            res["fetched"].append(s)
        else:
            res["failed"].append(s)
        if (i + 1) % 50 == 0:
            _out(f"  {i + 1}/{len(symbols)} done")
        time.sleep(sleep)
    return res


def report(symbols: list) -> dict:
    """Check the given symbols and merge the results into _report.json (other symbols kept)."""
    path = os.path.join(chart_dir(), "_report.json")
    out = _load(path).get("symbols", {})
    for s in symbols:
        rows = _load(os.path.join(chart_dir(), f"{s}_chart.json")).get("rows")
        if rows:
            out[s] = check(rows, rapid_closes(s))
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"written": datetime.datetime.now().isoformat(timespec="seconds"), "symbols": out}, f, indent=1)
    return {s: out[s] for s in symbols if s in out}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--symbols", nargs="*", help="default: every symbol folder in Data/Symbol")
    ap.add_argument("--force", action="store_true", help="re-fetch symbols already fetched today")
    ap.add_argument("--report", action="store_true", help="only re-check the saved files")
    ap.add_argument("--delay", type=float, default=SLEEP, help=f"seconds between calls (default {SLEEP})")
    a = ap.parse_args(argv)
    base = paths.symbol_dir()
    symbols = [s.upper() for s in a.symbols] if a.symbols else sorted(
        d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d)))
    stopped = False
    if not a.report:
        r = run(symbols, a.force, sleep=a.delay)
        stopped = bool(r["stopped"])
        _out(f"fetched {len(r['fetched'])}, skipped (already today) {len(r['skipped'])}, failed {len(r['failed'])}")
        if r["failed"]:
            _out("failed: " + ", ".join(r["failed"][:30]))
        if r["stopped"]:
            _out(f"STOPPED: {r['stopped']}")
    rep = report(symbols)
    bad = {s: v["problems"] for s, v in rep.items() if v["problems"]}
    _out(f"\ncontent check: {len(rep)} symbols with history, {len(bad)} with problems")
    for s, p in list(bad.items())[:20]:
        _out(f"  {s}: {'; '.join(p)}")
    _out(f"report: {os.path.join(chart_dir(), '_report.json')}")
    return 1 if stopped else 0


if __name__ == "__main__":
    sys.exit(main())
