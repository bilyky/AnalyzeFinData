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

Themes (see THEMES): each has a curated seed list and, optionally, SEC SIC codes that
pull in more universe symbols. The signals are the same for every theme.
  - ai_buildout:  power / cooling / electrical / construction (seed + SIC)
  - robot_vision: what robots need to see: sensors, vision chips, perception
                  software (seed only; SIC 3674/7372 would pull in every chip and
                  software company)
"""

import datetime
import json
import os
import time
import warnings
from pathlib import Path

import openpyxl
import requests
import urllib3

from aether import paths
from aether.config import CFG
from aether.logger import get_logger as _get_logger

_log = _get_logger("ai_buildout")

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

# Robot vision: eyes (lidar, radar, machine vision, thermal, image sensors), chips that
# process what the eyes capture, and perception / autonomy software. Robot makers
# themselves are left out. Foreign filers without quarterly XBRL (Innoviz, Hesai,
# Pony, WeRide) are left out because the SEC signals would all be empty.
ROBOT_VISION_SEED = {
    "OUST": "eyes", "AEVA": "eyes", "MVIS": "eyes", "ARBE": "eyes",
    "CGNX": "eyes", "ZBRA": "eyes", "TDY": "eyes", "ON": "eyes",
    "AMBA": "chips", "LSCC": "chips", "INDI": "chips", "CEVA": "chips",
    "MBLY": "software", "AUR": "software",
}

THEMES = {
    "ai_buildout": {
        "title": "AI-buildout supply chain",
        "buckets": ("power", "cooling", "electrical", "construction"),
        "seed": SEED_THEME,
        "sic": SIC_BUCKET,
    },
    "robot_vision": {
        "title": "Robot vision: eyes, chips, software",
        "buckets": ("eyes", "chips", "software"),
        "seed": ROBOT_VISION_SEED,
        "sic": {},
    },
}
DEFAULT_THEME = "ai_buildout"

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
# Revenue / RPO periods older than this are shown but not scored. Domestic filers
# report within ~45 days of quarter end; foreign filers (20-F) can be 9+ months old.
STALE_DAYS = 200

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
_CACHE_TTL_HOURS = 20
_REQUEST_GAP_S = 0.15   # SEC fair-access limit is 10 requests/second
_RETRIES = 2
_RETRY_BACKOFF_S = 5


def _theme(theme):
    if theme not in THEMES:
        raise ValueError(f"Unknown theme {theme!r}; known: {', '.join(THEMES)}")
    return THEMES[theme]


def bucket_for(symbol, sic=None, theme=DEFAULT_THEME):
    """Theme bucket for a symbol, or None. Seed list wins over the SIC code."""
    t = _theme(theme)
    s = (symbol or "").upper().strip()
    if s in t["seed"]:
        return t["seed"][s]
    try:
        return t["sic"].get(int(sic)) if sic not in (None, "") else None
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
# SEC's Akamai front end answers 403 when it rate-blocks the caller (or the shared
# corporate egress). After this many 403s in a row, stop requesting for the rest of
# the run and use cached copies only, instead of sending hundreds more blocked requests.
_BLOCK_AFTER_403S = 3
_consecutive_403 = [0]
_blocked = [False]
# Cache files served past their lifetime because a refresh failed. scan() turns
# these into a per-row label and one warning, so stale SEC data is never silent.
_stale_served = set()


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
    with warnings.catch_warnings():
        # Silence the insecure-request warning for this call only, not process-wide.
        warnings.simplefilter("ignore", urllib3.exceptions.InsecureRequestWarning)
        return requests.get(url, headers=headers, timeout=30, verify=False)


def _read_cache(cache_name: str):
    """Cached SEC JSON regardless of age, or None. No network."""
    path = _cache_dir() / cache_name
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except Exception as e:
        _log.warning(f"Bad EDGAR cache file {path.name}: {e}")
        return None


def _fetch(url: str):
    """One SEC GET with retries on 5xx / timeouts. Returns parsed JSON or None.
    Counts consecutive 403s and trips _blocked so the run stops requesting."""
    for attempt in range(_RETRIES + 1):
        wait = _REQUEST_GAP_S - (time.time() - _last_request[0])
        if wait > 0:
            time.sleep(wait)
        try:
            r = _http_get(url)
            _last_request[0] = time.time()
            if r.status_code == 403:
                _consecutive_403[0] += 1
                if _consecutive_403[0] >= _BLOCK_AFTER_403S and not _blocked[0]:
                    _blocked[0] = True
                    _log.error(f"EDGAR returned 403 {_consecutive_403[0]} times in a row "
                               "(rate block or missing contact in User-Agent; set "
                               "AETHER_SEC_CONTACT). No more SEC requests this run; "
                               "using cached data only.")
                else:
                    _log.warning(f"EDGAR 403 for {url}")
                return None
            _consecutive_403[0] = 0
            if r.status_code == 404:
                return None
            if r.status_code >= 500 and attempt < _RETRIES:
                _log.warning(f"EDGAR {r.status_code} for {url}; retrying.")
                time.sleep(_RETRY_BACKOFF_S * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        except requests.exceptions.Timeout as e:
            if attempt < _RETRIES:
                time.sleep(_RETRY_BACKOFF_S * (attempt + 1))
                continue
            _log.warning(f"EDGAR fetch timed out for {url}: {e}")
            return None
        except Exception as e:
            _log.warning(f"EDGAR fetch failed for {url}: {e}")
            return None
    return None


def _get_json(url: str, cache_name: str, ttl_hours: float = _CACHE_TTL_HOURS):
    """GET a SEC JSON document through a file cache. A fresh cache is used as is.
    Otherwise fetch; if the fetch fails (or SEC has blocked this run) fall back to
    the stale cached copy rather than losing the symbol. None if neither exists."""
    path = _cache_dir() / cache_name
    if path.exists() and (time.time() - path.stat().st_mtime) < ttl_hours * 3600:
        cached = _read_cache(cache_name)
        if cached is not None:
            return cached
    data = None if _blocked[0] else _fetch(url)
    if data is None:
        stale = _read_cache(cache_name)
        if stale is not None:
            _stale_served.add(cache_name)
            _log.debug(f"Using stale EDGAR cache for {cache_name}")
        return stale
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def ticker_cik_map() -> dict:
    data = _get_json(_TICKERS_URL, "company_tickers.json", ttl_hours=24 * 7) or {}
    return {v["ticker"].upper(): int(v["cik_str"]) for v in data.values()}


def submissions(cik: int, ttl_hours: float = _CACHE_TTL_HOURS):
    return _get_json(_SUBMISSIONS_URL.format(cik=cik), f"sub_{cik}.json", ttl_hours)


def company_facts(cik: int, ttl_hours: float = _CACHE_TTL_HOURS):
    return _get_json(_FACTS_URL.format(cik=cik), f"facts_{cik}.json", ttl_hours)


# ---------------------------------------------------------------------------
# Signal extraction (pure functions over SEC / OHLCV JSON)
# ---------------------------------------------------------------------------

def _d(s):
    return datetime.date.fromisoformat(s)


def _known_by(e, as_of):
    """True if the fact's period had ended AND it had been filed by as_of. Filtering
    on the filing date keeps a backtest point-in-time (a quarter is filed weeks after
    it ends, and restatements are filed later still)."""
    return not as_of or (e["end"] <= as_of and e.get("filed", "") <= as_of)


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
            if "start" not in e or not _known_by(e, as_of):
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
              if e.get("val") and _known_by(e, as_of)]
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
    stale = row.get("stale") or ()
    for key in ("revenue_yoy", "rpo_yoy"):
        v = row.get(key)
        if key in stale:
            continue
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


def build_row(symbol, bucket, sub, facts, ohlcv_ts, spy_bars, as_of, bars=None):
    """All signals for one symbol as a flat dict (missing data -> None). `bars` may
    be passed pre-sliced (real bars up to as_of) to skip re-parsing ohlcv_ts."""
    rev_end, rev = revenue_yoy(facts, as_of)
    rpo_end, rpo = rpo_yoy(facts, as_of)
    n_agr, agr_dates = material_agreements(sub, as_of)
    if bars is None:
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
    row["stale"] = [key for key, end in (("revenue_yoy", rev_end), ("rpo_yoy", rpo_end))
                    if end and (_d(as_of) - _d(end)).days > STALE_DAYS]
    row["rpo_suspect"] = rpo is not None and abs(rpo) > RPO_SUSPECT_PCT
    row["watch_score"] = watch_score(row)
    return row


# ---------------------------------------------------------------------------
# Universe scan
# ---------------------------------------------------------------------------

RESEARCH_SHEET = "Research"
RESEARCH_SYMBOL_COL = 3   # column D, the same column the game reads


def load_universe(path=None):
    """Symbols on the Research sheet of Data/state_of_the_day.xlsx: the same list the
    game trades from (ai_portfolio_game._active_setup_symbols reads column D of it).
    Order kept, duplicates and blanks dropped."""
    path = Path(path or Path(paths.data_dir()) / "state_of_the_day.xlsx")
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb[RESEARCH_SHEET]
        syms = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            v = row[RESEARCH_SYMBOL_COL] if len(row) > RESEARCH_SYMBOL_COL else None
            if v is not None and str(v).strip():
                syms.append(str(v).strip().upper())
    finally:
        wb.close()
    return list(dict.fromkeys(syms))


def _load_ohlcv(symbol):
    p = Path(paths.ohlcv_dir()) / f"{symbol}_daily.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("Time Series (Daily)", {})
    except Exception as e:
        _log.warning(f"Could not read OHLCV for {symbol}: {e}")
        return None


def scan(as_of, universe=None, failed=None, theme=DEFAULT_THEME, stale=None):
    """Classify universe + seed names into the theme's buckets and compute signals.
    A seed-only theme (no SIC codes) scans just its seed list. Returns a list of
    rows sorted by watch_score (high first). Symbols whose SEC submissions could
    not be fetched are appended to `failed` (if given) so the caller can report
    them instead of dropping them silently. Rows built from an expired SEC cache
    (refresh failed) get sec_cache_stale=True, are appended to `stale` (if given),
    and are summarized in one warning."""
    t = _theme(theme)
    _stale_served.clear()
    stale = [] if stale is None else stale
    cik_map = ticker_cik_map()
    if not cik_map:
        raise RuntimeError("SEC ticker map unavailable; cannot scan.")
    base = (universe or load_universe()) if t["sic"] else []
    symbols = list(dict.fromkeys(base + list(t["seed"])))
    spy_bars = _real_bars(_load_ohlcv("SPY") or {}, as_of)

    rows = []
    for sym in symbols:
        cik = cik_map.get(sym)
        if not cik:
            continue   # ETFs and non-SEC filers have no CIK
        sub = submissions(cik)
        if sub is None and failed is not None:
            failed.append(sym)
        bucket = bucket_for(sym, (sub or {}).get("sic"), theme)
        if not bucket:
            continue
        row = build_row(sym, bucket, sub, company_facts(cik), _load_ohlcv(sym), spy_bars, as_of)
        row["sec_cache_stale"] = bool({f"sub_{cik}.json", f"facts_{cik}.json"} & _stale_served)
        if row["sec_cache_stale"]:
            stale.append(sym)
        rows.append(row)
    if stale:
        _log.warning(f"SEC refresh failed for {len(stale)} symbol(s); their rows use older "
                     f"cached SEC data (labeled): {', '.join(stale)}")
    rows.sort(key=lambda r: (-r["watch_score"], r["symbol"]))
    return rows


def output_path(theme=DEFAULT_THEME, suffix=".json") -> Path:
    """Data/<theme>_watch.json (or .html)."""
    _theme(theme)
    return Path(paths.data_dir()) / f"{theme}_watch{suffix}"


def save(rows, as_of, out_path=None, failed=None, theme=DEFAULT_THEME, stale=None):
    out_path = Path(out_path or output_path(theme))
    out_path.write_text(json.dumps({"as_of": as_of, "theme": theme,
                                    "title": THEMES[theme]["title"], "rows": rows,
                                    "fetch_failed": sorted(failed or []),
                                    "sec_cache_stale": sorted(stale or [])}, indent=2),
                        encoding="utf-8")
    return out_path


def load_latest(path=None, theme=DEFAULT_THEME):
    """Last saved scan, or None. Used by the web endpoint."""
    path = Path(path or output_path(theme))
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))

