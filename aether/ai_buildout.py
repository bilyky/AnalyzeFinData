"""
AI-buildout supply chain: theme map + leading-signal ranking (WATCH-ONLY).

Idea: AI data centers are limited by power, cooling, electrical gear and construction
capacity, not chips. Money spent on AI flows to these suppliers, and a single big
contract can move a small supplier's numbers (example: Solaris / SEI and xAI's
mobile-turbine order). This module finds those names and ranks them by public,
early signals that a supplier's results are growing:

  - quarterly revenue YoY growth        (SEC XBRL companyfacts)
  - remaining performance obligation YoY (backlog proxy, SEC XBRL)
  - 8-K item 1.01 filings in the last 90 days (material agreements, SEC submissions)
  - 60-day return vs SPY                 (local OHLCV)
  - 20-day Chaikin money flow            (local OHLCV, real-volume bars only)

The ranking is NOT validated and adds no buy weight. It is a watchlist only until a
backtest shows the signals have a forward-return spread.

Theme membership: a curated seed list first, then the company's SEC SIC code.
"""

import datetime
import json
import os
import time
from pathlib import Path

import requests
import urllib3

from aether import paths
from aether.config import CFG
from aether.logger import get_logger as _get_logger

_log = _get_logger("ai_buildout")

BUCKETS = ("power", "cooling", "electrical", "construction")

# Curated names with known AI-data-center exposure. Takes precedence over SIC codes.
SEED_THEME = {
    # power: generation, turbines, fuel cells, nuclear
    "SEI": "power", "GEV": "power", "VST": "power", "CEG": "power", "NRG": "power",
    "TLN": "power", "BE": "power", "OKLO": "power", "SMR": "power",
    # cooling / thermal
    "VRT": "cooling", "MOD": "cooling", "TT": "cooling", "JCI": "cooling",
    # electrical gear, cable, connectors, fiber
    "ETN": "electrical", "HUBB": "electrical", "POWL": "electrical", "NVT": "electrical",
    "APH": "electrical", "GLW": "electrical",
    # construction, grid contractors, aggregates/concrete
    "PWR": "construction", "MTZ": "construction", "EME": "construction",
    "FIX": "construction", "DY": "construction", "STRL": "construction",
    "PRIM": "construction", "MLM": "construction", "VMC": "construction",
}

# SEC SIC code -> bucket (used for universe symbols not in the seed list).
SIC_BUCKET = {
    # power
    3511: "power", 3621: "power", 4911: "power", 4922: "power", 4923: "power",
    4931: "power", 4991: "power",
    # cooling
    3585: "cooling",
    # electrical
    3357: "electrical", 3612: "electrical", 3613: "electrical", 3643: "electrical",
    3678: "electrical", 3690: "electrical",
    # construction (1400 mining and 1700 general trades are too broad: lithium miners,
    # residential solar installers)
    1600: "construction", 1623: "construction", 1731: "construction",
    3241: "construction", 8711: "construction",
}

_REVENUE_TAGS = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "SalesRevenueNet",
)
_RPO_TAG = "RevenueRemainingPerformanceObligation"
# RPO swings beyond this are usually a reporting change or an acquisition, not
# organic backlog growth (e.g. AES $7M -> $435M, CEG $1.0B -> $12.0B). Shown, not scored.
RPO_SUSPECT_PCT = 300

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
_CACHE_TTL_HOURS = 20
_REQUEST_GAP_S = 0.15   # SEC fair-access limit is 10 requests/second
_RETRIES = 2
_RETRY_BACKOFF_S = 5


def bucket_for(symbol, sic=None):
    """Theme bucket for a symbol, or None. Seed list wins over the SIC code."""
    s = (symbol or "").upper().strip()
    if s in SEED_THEME:
        return SEED_THEME[s]
    try:
        return SIC_BUCKET.get(int(sic)) if sic not in (None, "") else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# SEC EDGAR access (file cache, polite rate limit)
# ---------------------------------------------------------------------------

def _cache_dir() -> Path:
    return Path(paths.data_dir()) / "edgar_cache"


def _user_agent() -> str:
    """SEC fair-access policy requires a User-Agent with a contact email; requests
    without a valid-looking one get HTTP 403."""
    contact = os.environ.get("AETHER_SEC_CONTACT", "")
    if not contact:
        try:
            contact = CFG.email_sender_address or ""
        except Exception:
            contact = ""
    return f"AnalyzeFinData research {contact or 'analyzefindata@users.noreply.github.com'}"


_last_request = [0.0]
_tls_fallback = [False]


def _http_get(url):
    """GET with TLS verification. A TLS-intercepting corporate proxy breaks
    verification; this is public read-only data, so on an SSL error fall back to
    an unverified request (same trade-off as aether.etrade) and warn once."""
    headers = {"User-Agent": _user_agent()}
    if not _tls_fallback[0]:
        try:
            return requests.get(url, headers=headers, timeout=30)
        except requests.exceptions.SSLError as e:
            _log.warning(f"EDGAR TLS verification failed ({e.__class__.__name__}); "
                         "using unverified requests for this run (public data).")
            _tls_fallback[0] = True
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    return requests.get(url, headers=headers, timeout=30, verify=False)


def _get_json(url: str, cache_name: str, ttl_hours: float = _CACHE_TTL_HOURS):
    """GET a SEC JSON document through a file cache. Returns None on failure."""
    path = _cache_dir() / cache_name
    if path.exists() and (time.time() - path.stat().st_mtime) < ttl_hours * 3600:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            _log.warning(f"Bad EDGAR cache file {path.name}, refetching: {e}")
    data = None
    for attempt in range(_RETRIES + 1):
        wait = _REQUEST_GAP_S - (time.time() - _last_request[0])
        if wait > 0:
            time.sleep(wait)
        try:
            r = _http_get(url)
            _last_request[0] = time.time()
            if r.status_code == 404:
                return None
            if r.status_code >= 500 and attempt < _RETRIES:
                _log.warning(f"EDGAR {r.status_code} for {url}; retrying.")
                time.sleep(_RETRY_BACKOFF_S * (attempt + 1))
                continue
            r.raise_for_status()
            data = r.json()
            break
        except requests.exceptions.Timeout as e:
            if attempt < _RETRIES:
                time.sleep(_RETRY_BACKOFF_S * (attempt + 1))
                continue
            _log.warning(f"EDGAR fetch timed out for {url}: {e}")
            return None
        except Exception as e:
            _log.warning(f"EDGAR fetch failed for {url}: {e}")
            return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def ticker_cik_map() -> dict:
    data = _get_json(_TICKERS_URL, "company_tickers.json", ttl_hours=24 * 7) or {}
    return {v["ticker"].upper(): int(v["cik_str"]) for v in data.values()}


def submissions(cik: int):
    return _get_json(_SUBMISSIONS_URL.format(cik=cik), f"sub_{cik}.json")


def company_facts(cik: int):
    return _get_json(_FACTS_URL.format(cik=cik), f"facts_{cik}.json")


# ---------------------------------------------------------------------------
# Signal extraction (pure functions over SEC / OHLCV JSON)
# ---------------------------------------------------------------------------

def _d(s):
    return datetime.date.fromisoformat(s)


def _latest_per_end(entries):
    """One value per period end; when restated, keep the most recently filed."""
    by_end = {}
    for e in entries:
        prev = by_end.get(e["end"])
        if prev is None or e.get("filed", "") > prev.get("filed", ""):
            by_end[e["end"]] = e
    return by_end


def _find_year_ago(by_end, end):
    target = _d(end) - datetime.timedelta(days=365)
    best = None
    for e_end, e in by_end.items():
        gap = abs((_d(e_end) - target).days)
        if gap <= 20 and (best is None or gap < best[0]):
            best = (gap, e)
    return best[1] if best else None


def _yoy(cur, prev):
    if not prev or not prev.get("val"):
        return None
    return round((cur["val"] - prev["val"]) / abs(prev["val"]) * 100, 1)


def revenue_yoy(facts, as_of=None):
    """(latest quarter end, YoY % change) for quarterly revenue, or (None, None).
    Uses whichever revenue tag has the most recent quarter."""
    gaap = (facts or {}).get("facts", {}).get("us-gaap", {})
    best = (None, None)
    for tag in _REVENUE_TAGS:
        quarters = []
        for e in gaap.get(tag, {}).get("units", {}).get("USD", []):
            if "start" not in e or (as_of and e["end"] > as_of):
                continue
            if 80 <= (_d(e["end"]) - _d(e["start"])).days <= 100:
                quarters.append(e)
        if not quarters:
            continue
        by_end = _latest_per_end(quarters)
        end = max(by_end)
        if best[0] is not None and end <= best[0]:
            continue
        best = (end, _yoy(by_end[end], _find_year_ago(by_end, end)))
    return best


def rpo_yoy(facts, as_of=None):
    """(latest end, YoY % change) of remaining performance obligation, or (None, None)."""
    gaap = (facts or {}).get("facts", {}).get("us-gaap", {})
    points = [e for e in gaap.get(_RPO_TAG, {}).get("units", {}).get("USD", [])
              if e.get("val") and not (as_of and e["end"] > as_of)]
    if not points:
        return (None, None)
    by_end = _latest_per_end(points)
    end = max(by_end)
    return (end, _yoy(by_end[end], _find_year_ago(by_end, end)))


def material_agreements(sub, as_of, days=90):
    """8-K filings with item 1.01 (material definitive agreement) in the window
    (as_of - days, as_of]. Returns (count, [filing dates, newest first])."""
    recent = (sub or {}).get("filings", {}).get("recent", {})
    start = (_d(as_of) - datetime.timedelta(days=days)).isoformat()
    hits = []
    for form, date, items in zip(recent.get("form", []), recent.get("filingDate", []),
                                 recent.get("items", [])):
        if form == "8-K" and start < date <= as_of and "1.01" in (items or "").split(","):
            hits.append(date)
    hits.sort(reverse=True)
    return len(hits), hits


def _real_bars(ohlcv_ts, as_of):
    """Bars at or before as_of, oldest first, skipping provisional / zero-volume /
    flat placeholder bars (they carry no real price or volume information)."""
    out = []
    for date in sorted(ohlcv_ts or {}):
        if date > as_of:
            break
        b = ohlcv_ts[date]
        try:
            hi, lo, c = float(b["2. high"]), float(b["3. low"]), float(b["4. close"])
            v = float(b.get("5. volume", 0) or 0)
        except (KeyError, TypeError, ValueError):
            continue
        if b.get("provisional") or v <= 0 or hi <= lo:
            continue
        out.append((date, hi, lo, c, v))
    return out


def return_pct(bars, n):
    if len(bars) <= n:
        return None
    return (bars[-1][3] / bars[-1 - n][3] - 1) * 100


def chaikin_money_flow(bars, n=20):
    """Classic CMF over the last n real bars, in [-1, 1], or None."""
    if len(bars) < n:
        return None
    mfv = vol = 0.0
    for _, hi, lo, c, v in bars[-n:]:
        mfv += ((c - lo) - (hi - c)) / (hi - lo) * v
        vol += v
    return round(mfv / vol, 3) if vol else None


def watch_score(row):
    """Simple points ranking of the leading signals. Unvalidated; for sorting only."""
    pts = 0
    for key in ("revenue_yoy", "rpo_yoy"):
        v = row.get(key)
        if key == "rpo_yoy" and v is not None and abs(v) > RPO_SUSPECT_PCT:
            continue
        if v is not None:
            pts += 2 if v >= 25 else 1 if v >= 10 else -1 if v < 0 else 0
    n = row.get("agreements_90d") or 0
    pts += 2 if n >= 2 else 1 if n == 1 else 0
    rs = row.get("rs_60d")
    if rs is not None:
        pts += 2 if rs >= 10 else 1 if rs > 0 else -1
    cmf = row.get("cmf_20")
    if cmf is not None:
        pts += 1 if cmf >= 0.05 else -1 if cmf <= -0.05 else 0
    return pts


def build_row(symbol, bucket, sub, facts, ohlcv_ts, spy_bars, as_of):
    """All signals for one symbol as a flat dict (missing data -> None)."""
    rev_end, rev = revenue_yoy(facts, as_of)
    rpo_end, rpo = rpo_yoy(facts, as_of)
    n_agr, agr_dates = material_agreements(sub, as_of)
    bars = _real_bars(ohlcv_ts, as_of) if ohlcv_ts else []
    r60, spy60 = return_pct(bars, 60), return_pct(spy_bars, 60)
    row = {
        "symbol": symbol,
        "bucket": bucket,
        "name": (sub or {}).get("name"),
        "revenue_q_end": rev_end, "revenue_yoy": rev,
        "rpo_end": rpo_end, "rpo_yoy": rpo,
        "agreements_90d": n_agr, "agreement_dates": agr_dates[:5],
        "last_bar": bars[-1][0] if bars else None,
        "close": round(bars[-1][3], 2) if bars else None,
        "rs_60d": round(r60 - spy60, 1) if r60 is not None and spy60 is not None else None,
        "cmf_20": chaikin_money_flow(bars),
    }
    row["watch_score"] = watch_score(row)
    return row


# ---------------------------------------------------------------------------
# Universe scan
# ---------------------------------------------------------------------------

def load_universe(path=None):
    """Symbols from Data/symbols_to_check.txt (tab-separated, symbol is the last column)."""
    path = Path(path or Path(paths.data_dir()) / "symbols_to_check.txt")
    syms = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.strip().split("\t")
        if parts and parts[-1].strip():
            syms.append(parts[-1].strip().upper())
    return syms


def _load_ohlcv(symbol):
    p = Path(paths.data_dir()) / "Symbol_full" / f"{symbol}_daily.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("Time Series (Daily)", {})
    except Exception as e:
        _log.warning(f"Could not read OHLCV for {symbol}: {e}")
        return None


def scan(as_of, universe=None, failed=None):
    """Classify universe + seed names into buckets and compute signals.
    Returns a list of rows sorted by watch_score (high first). Symbols whose SEC
    submissions could not be fetched are appended to `failed` (if given) so the
    caller can report them instead of dropping them silently."""
    cik_map = ticker_cik_map()
    if not cik_map:
        raise RuntimeError("SEC ticker map unavailable; cannot scan.")
    symbols = list(dict.fromkeys((universe or load_universe()) + list(SEED_THEME)))
    spy_bars = _real_bars(_load_ohlcv("SPY") or {}, as_of)

    rows = []
    for sym in symbols:
        cik = cik_map.get(sym)
        if not cik:
            continue   # ETFs and non-SEC filers have no CIK
        sub = submissions(cik)
        if sub is None and failed is not None:
            failed.append(sym)
        bucket = bucket_for(sym, (sub or {}).get("sic"))
        if not bucket:
            continue
        rows.append(build_row(sym, bucket, sub, company_facts(cik),
                              _load_ohlcv(sym), spy_bars, as_of))
    rows.sort(key=lambda r: (-r["watch_score"], r["symbol"]))
    return rows


def save(rows, as_of, out_path=None, failed=None):
    out_path = Path(out_path or Path(paths.data_dir()) / "ai_buildout_watch.json")
    out_path.write_text(json.dumps({"as_of": as_of, "rows": rows,
                                    "fetch_failed": sorted(failed or [])}, indent=2),
                        encoding="utf-8")
    return out_path


def load_latest(path=None):
    """Last saved scan, or None. Used by the web endpoint."""
    path = Path(path or Path(paths.data_dir()) / "ai_buildout_watch.json")
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))

