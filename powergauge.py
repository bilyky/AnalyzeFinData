import datetime
import re
import requests
import json
import os
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from utils import _to_float

from excel_output import (
    write_research_headers      as _write_research_headers,
    write_picks_sheet           as _write_picks_sheet,
    update_short_long_scores    as _update_short_long_scores,
    update_replacements_sheet   as _update_replacements_sheet,
    fix_comment_shape_ids       as _fix_comment_shape_ids,
    backup_xlsx                 as _backup_xlsx,
)
from scoring import (
    REGIME_SYMBOL,
    ohlcv_streak_perc    as _ohlcv_streak_perc,
    ohlcv_streak_count   as _ohlcv_streak_count,
    week_of_month        as _week_of_month,
    compute_seasonality  as _compute_seasonality,
    predicted_win_pct    as _predicted_win_pct,
    market_regime        as _market_regime,
    rel_volume_bucket    as _rel_volume_bucket,
    fibonacci_retracement_score as _fib_score,
    rsi_divergence_score        as _rsi_div_score,
    short_score          as _short_score_fn,
    long_score           as _long_score_fn,
)
from patterns import (
    candlestick_score    as _cs_score,
    chart_pattern_score  as _cp_score,
    momentum_pattern_score as _mo_score,
    pattern_summary      as _pattern_summary,
)

PGR_STR = ["", "Be-", "Be", "N", "Bu", "Bu+", ""]


def _pgr_str(v: int) -> str:
    if 0 <= v < len(PGR_STR):
        return PGR_STR[v]
    return ""

# Pre-built index of symbol → sorted list of cached JSON paths.
# None = not yet scanned; {} = scanned but empty directory.
_cache_file_index: dict | None = None


def _build_cache_index():
    """Scan Data/Symbol recursively and build a symbol→[paths] index for find_prev_pf."""
    global _cache_file_index
    if _cache_file_index is not None:
        return
    symbol_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Data", "Symbol")
    from collections import defaultdict
    idx: dict = defaultdict(list)
    try:
        for root, _dirs, files in os.walk(symbol_dir):
            for name in files:
                if not name.endswith('.json'):
                    continue
                sym = name.rsplit('_', 1)[0]
                idx[sym].append(os.path.join(root, name))
    except OSError:
        pass
    _cache_file_index = {sym: sorted(paths) for sym, paths in idx.items()}
    print(f"Cache index built: {len(_cache_file_index)} symbols")


SESSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Data", "session.txt")
_PROXY_URL = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
_PROXIES = {"http": _PROXY_URL, "https": _PROXY_URL} if _PROXY_URL else {}

_http_session: requests.Session | None = None


def _get_http_session() -> requests.Session:
    """Return a shared Session with retry logic and proxy pre-configured."""
    global _http_session
    if _http_session is None:
        _http_session = requests.Session()
        _retry = Retry(total=1, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=_retry)
        _http_session.mount("https://", adapter)
        _http_session.mount("http://", adapter)
        _http_session.verify = False
        if _PROXIES:
            _http_session.proxies.update(_PROXIES)
    return _http_session


SRC_XLSX  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state_of_the_day.xlsx")
XLSX_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Data", "state_of_the_day.xlsx")
XLSX_BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Data", "Backup")
OHLCV_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Data", "Symbol_full")

# ── Chaikin API ───────────────────────────────────────────────────────────────
# Client API key embedded in Chaikin's own web app; override via env var if it rotates.
_CHAIKIN_API_KEY = os.environ.get(
    "CHAIKIN_API_KEY",
    "76J!7fb?jhEtz/hd7i6rHPKklawGZb5VLReDQXa0?4-jGCqQFi74xYCsb0H-hqUC",
)
# Concurrent workers for parallel symbol fetch in check_from_xls.
_FETCH_WORKERS = int(os.environ.get("CHAIKIN_WORKERS", "10"))

# ── Symbol validation ─────────────────────────────────────────────────────────
_SYMBOL_RE = re.compile(r"^[A-Z0-9._\-]+$")

# ── OHLCV / entry-filter parameters ──────────────────────────────────────────
_STOP_LOOKBACK_DAYS   = 3    # min-low window for stop price
_TARGET_LOOKBACK_DAYS = 10   # max-high window for target price
_TREND_SMA_PERIOD     = 20   # SMA period for trend filter
_DIR_CHECK_DAYS       = 3    # "price above N days ago" direction check
SESSION_INSTRUCTIONS = """
Session expired or missing. To get a new session token:
  1. Open https://app.chaikinanalytics.com in your browser and log in.
  2. Press F12 to open DevTools, go to the Network tab.
  3. Click on any API request (e.g. getSymbolData or getChecklistStocks).
  4. In the Request Headers, find the 'Cookie' header.
  5. Copy the value of JSESSIONID (the part after 'JSESSIONID=' and before ';').
  6. Save that value to: {session_file}
Then re-run the script.
""".strip()

class PowerGauge:
    def __init__(self, symbol, date=None):
        self.symbol = symbol
        self.date = date if date is not None else datetime.date.today()
        self.pgr_value = 0
        self.pgr_corrected_value = 0
        self.industry_name = ""
        self.price = 0.0
        self.max_price = 0.0
        self.signals = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
        self.percentage = 0.0
        self.change = 0.0
        self.prevPG: PowerGauge | None = None
        self.industry_strength = ""
        self.lt_trend = ""
        self.money_flow = ""
        self.over_bt_sl = ""
        self.relative_strength = ""

    def init_from_json(self, data_json, check_schema=True):
        cl = data_json.get('checklist_stocks') or {}

        # Early-out: invalid symbol reported by API
        if data_json.get('status') == 'invalid symbol':
            self.price = -1
            if check_schema:
                self._check_schema(data_json)
            return

        pgr_list = data_json.get('pgr') or []
        self.pgr_value = (
            pgr_list[0].get('PGR Value', 0) if len(pgr_list) > 0
            else cl.get('rawPgrRating', 0)
        )
        self.pgr_corrected_value = (
            pgr_list[5].get('Corrected PGR Value', 0) if len(pgr_list) > 5
            else cl.get('pgrRating', 0)
        )
        metainfo = data_json.get('metaInfo') or [{}]
        m = metainfo[0]
        industry = (m.get('industry_name') or m.get('etf_group_name') or m.get('industry_logo_name')
                    or (m.get('etf_data') or {}).get('list_name')
                    or m.get('name') or '')
        self.industry_name = industry.replace(',', '')
        self.price = m.get('Last') if m.get('Last') is not None else _to_float(cl.get('lastPrice'), -1)
        self.max_price = self.price
        self.signals = m.get('signals')
        self.percentage = m.get('Percentage ') if m.get('Percentage ') is not None else _to_float(cl.get('changePercentage'), 0)
        self.change = m.get('Change') if m.get('Change') is not None else _to_float(cl.get('change'), None)
        self.industry_strength = cl.get('industry')
        self.lt_trend = cl.get('ltTrend')
        self.money_flow = cl.get('moneyFlow')
        self.over_bt_sl = cl.get('overboughtOversold')
        # relativeStrength: prefer checklist_stocks string; fall back to pgr[3] numeric score
        rs_str = cl.get('relativeStrength')
        pgr_list = data_json.get('pgr') or []
        if rs_str:
            self.relative_strength = rs_str
        elif len(pgr_list) > 3:
            technicals = pgr_list[3].get('Technicals') or []
            rs_score = next((t.get('Rel Strength vs Market') for t in technicals if 'Rel Strength vs Market' in t), None)
            self.relative_strength = str(rs_score) if rs_score is not None else ""
        if check_schema:
            self._check_schema(data_json)

    def _check_schema(self, data_json):
        warnings = []
        pgr_list = data_json.get('pgr') or []
        if not pgr_list:
            warnings.append("'pgr' list missing or empty")
        elif len(pgr_list) <= 5:
            warnings.append(f"'pgr' list shorter than expected (len={len(pgr_list)}, need >=6)")
        else:
            if 'PGR Value' not in pgr_list[0]:
                warnings.append("pgr[0] missing 'PGR Value'")
            if 'Corrected PGR Value' not in pgr_list[5]:
                warnings.append("pgr[5] missing 'Corrected PGR Value'")
        metainfo = data_json.get('metaInfo') or []
        if not metainfo:
            warnings.append("'metaInfo' list missing or empty")
        else:
            for key in ('Last', 'Percentage ', 'Change', 'signals'):
                if key not in metainfo[0]:
                    warnings.append(f"metaInfo[0] missing key '{key}'")
            if not any(k in metainfo[0] for k in ('industry_name', 'etf_group_name', 'industry_logo_name')):
                if not ((metainfo[0].get('etf_data') or {}).get('list_name') or metainfo[0].get('name')):
                    warnings.append("metaInfo[0] missing industry key (industry_name/etf_group_name/industry_logo_name/name)")
        cl = data_json.get('checklist_stocks') or {}
        if not cl:
            warnings.append("'checklist_stocks' missing or empty")
        else:
            for key in ('industry', 'ltTrend', 'moneyFlow', 'overboughtOversold'):
                if key not in cl:
                    warnings.append(f"checklist_stocks missing key '{key}'")
        if warnings:
            print(f"  [SCHEMA WARNING] {self.symbol}: " + "; ".join(warnings))

    def init_from_ohlcv(self, entry: dict):
        """Populate price fields from an Alpha Vantage OHLCV daily entry."""
        close = _to_float(entry.get('4. close'), -1)
        prev_close = _to_float(entry.get('prev_close'), close)
        self.price = close
        self.max_price = _to_float(entry.get('2. high'), close)
        self.change = round(close - prev_close, 4) if prev_close else 0
        self.percentage = round((self.change / prev_close) * 100, 4) if prev_close else 0

    def find_prev_pf(self):
        if self.prevPG is not None:
            return
        if _cache_file_index is None:
            _build_cache_index()
        candidates = (_cache_file_index or {}).get(self.symbol, [])
        today_str = str(self.date)

        # 1. Try Chaikin cache (most recent file before today)
        for path in reversed(candidates):
            fname = os.path.basename(path)
            date_str = fname[len(self.symbol) + 1:-5]
            if date_str < today_str:
                try:
                    prev_date = datetime.date.fromisoformat(date_str)
                except ValueError:
                    continue
                try:
                    with open(path, "r") as f:
                        data_jsn = json.load(f)
                except (json.JSONDecodeError, OSError) as e:
                    print(f"  [CACHE] {self.symbol}: skipping corrupt cache {path}: {e}")
                    continue
                self.prevPG = PowerGauge(self.symbol, prev_date)
                self.prevPG.init_from_json(data_jsn, check_schema=False)
                return

        # 2. Fall back to OHLCV data (Symbol_full/{symbol}_daily.json)
        ohlcv_file = os.path.join(OHLCV_DIR, f"{self.symbol}_daily.json")
        if not os.path.exists(ohlcv_file):
            return
        with open(ohlcv_file) as f:
            ohlcv = json.load(f)
        ts = ohlcv.get('Time Series (Daily)') or {}
        past_dates = sorted((d for d in ts if d < today_str), reverse=True)
        if not past_dates:
            return
        prev_date_str = past_dates[0]
        # Attach previous close so change/percentage are meaningful
        entry = dict(ts[prev_date_str])
        if len(past_dates) > 1:
            entry['prev_close'] = ts[past_dates[1]].get('4. close')
        self.prevPG = PowerGauge(self.symbol, datetime.date.fromisoformat(prev_date_str))
        self.prevPG.init_from_ohlcv(entry)

    def get_prev_same_move_count(self, _depth: int = 0) -> int:
        if _depth > 30:
            return -1 if self.percentage < 0 else 1
        if not self.prevPG:
            self.find_prev_pf()
        if self.prevPG:
            if self.percentage > 0 and self.prevPG.percentage > 0:
                return self.prevPG.get_prev_same_move_count(_depth + 1) + 1
            if self.percentage < 0 and self.prevPG.percentage < 0:
                return self.prevPG.get_prev_same_move_count(_depth + 1) - 1
            return -1 if self.percentage < 0 else 1
        return 0

    def get_prev_same_move_percent(self, _depth: int = 0) -> float:
        if _depth > 30:
            return self.percentage
        if not self.prevPG:
            self.find_prev_pf()
        if self.prevPG:
            if self.percentage > 0 and self.prevPG.percentage > 0:
                return (self.prevPG.get_prev_same_move_percent(_depth + 1) or self.prevPG.percentage) + self.percentage
            if self.percentage < 0 and self.prevPG.percentage < 0:
                return (self.prevPG.get_prev_same_move_percent(_depth + 1) or self.prevPG.percentage) + self.percentage
        return 0

    def get_prev_same_move_price(self, _depth: int = 0) -> float:
        if _depth > 30:
            return self.price
        if not self.prevPG:
            self.find_prev_pf()
        if self.change and self.prevPG and self.prevPG.change:
            if self.change > 0 and self.prevPG.change > 0:
                return self.prevPG.get_prev_same_move_price(_depth + 1) or self.prevPG.price
            if self.change < 0 and self.prevPG.change < 0:
                return self.prevPG.get_prev_same_move_price(_depth + 1) or self.prevPG.price
        return 0

    def get_prev_max_price(self, cur_price):
        if not self.prevPG:
            self.find_prev_pf()
        min_pr = self.get_prev_min_of(deep=3)
        local_max = max(self.max_price, self.get_prev_max_of(deep=3).price)
        if not min_pr.prevPG:
            min_pr.find_prev_pf()
        if min_pr.prevPG:
            if min_pr.price < cur_price:
                return min_pr.prevPG.get_prev_max_price(cur_price)
            return max(min_pr.get_prev_same_move_price() or min_pr.price, local_max)
        return local_max

    def get_prev_min_of(self, deep=3):
        if not self.prevPG:
            self.find_prev_pf()
        if self.prevPG:
            self.max_price = max(self.prevPG.max_price, self.max_price)
            pr = self
            if deep > 0:
                pr = self.prevPG.get_prev_min_of(deep-1)
            if pr.price < self.price:
                return pr
        return self

    def get_prev_max_of(self, deep=3):
        if not self.prevPG:
            self.find_prev_pf()
        if self.prevPG:
            pr = self
            if deep > 0:
                pr = self.prevPG.get_prev_max_of(deep-1)
            if pr.price >= self.price:
                return pr
        return self


def _load_session_from_file() -> str:
    if not os.path.exists(SESSION_FILE):
        return ""
    with open(SESSION_FILE, "r") as f:
        return f.read().strip()


def _save_session_to_file(session_id: str):
    os.makedirs(os.path.dirname(SESSION_FILE), exist_ok=True)
    with open(SESSION_FILE, "w") as f:
        f.write(session_id)


def _validate_session(session_id: str) -> bool:
    test_url = "https://members-backend.chaikinanalytics.com/CPTRestSecure/app/portfolio/getSymbolData" \
               "?uid=1101733&symbol=AAPL&components=pgr"
    headers = {'Cookie': f'JSESSIONID={session_id};'}
    try:
        r = _get_http_session().get(test_url, headers=headers, timeout=(5, 15))
        return r.status_code == 200
    except (requests.Timeout, requests.ConnectionError, requests.RequestException):
        return False


def _jwt_to_session_id(jwt_token: str) -> str:
    url = ("https://members-backend.chaikinanalytics.com/CPTRestSecure/app"
           "/authenticate/getJWTAuthorization?acquireSessionForcibly=Yes"
           f"&jwtToken={jwt_token}")
    headers = {
        'X-Api-Key': _CHAIKIN_API_KEY,
        'X-App-Id': 'omni',
    }
    r = _get_http_session().get(url, headers=headers, timeout=(5, 15))
    if not r.ok:
        raise EnvironmentError(f"JWT exchange failed: HTTP {r.status_code}")
    session_id = r.json().get('sessionId')
    if not session_id:
        raise EnvironmentError(f"No sessionId in JWT exchange response: {r.text[:200]}")
    return session_id


CREDENTIALS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chaikin_config.json")


def _load_credentials() -> tuple[str, str]:
    """Load Chaikin credentials: env vars (CHAIKIN_EMAIL/CHAIKIN_PASSWORD) take priority,
    then chaikin_config.json in the project root."""
    email    = os.environ.get('CHAIKIN_EMAIL', '')
    password = os.environ.get('CHAIKIN_PASSWORD', '')
    if email and password:
        return email, password
    if os.path.exists(CREDENTIALS_FILE):
        try:
            with open(CREDENTIALS_FILE) as f:
                cfg = json.load(f)
        except Exception as e:
            raise EnvironmentError(f"Failed to parse {CREDENTIALS_FILE}: {e}") from e
        missing = {'email', 'password'} - set(cfg.keys())
        if missing:
            raise EnvironmentError(f"Missing required keys in {CREDENTIALS_FILE}: {missing}")
        email    = cfg['email']
        password = cfg['password']
    if not email or not password:
        raise EnvironmentError(
            "Chaikin credentials not found.\n"
            "  Option 1: set env vars CHAIKIN_EMAIL and CHAIKIN_PASSWORD\n"
            f"  Option 2: copy chaikin_config.json.example -> {CREDENTIALS_FILE} and fill in values"
        )
    return email, password


def _login_via_browser() -> str:
    from playwright.sync_api import sync_playwright

    print("Opening browser for login (a window will appear)...")
    session_id = [None]

    def on_response(response):
        if 'getJWTAuthorization' in response.url and response.status == 200:
            try:
                data = response.json()
                sid = data.get('sessionId')
                if sid and sid != 'NULL':
                    session_id[0] = sid
                    print(f"  Session ID captured from browser.")
            except Exception as ex:
                print(f"  getJWTAuthorization parse error: {ex}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            channel='chrome',
            args=['--disable-blink-features=AutomationControlled'],
        )
        context = browser.new_context()
        page = context.new_page()
        page.on('response', on_response)

        page.goto('https://members.chaikinanalytics.com/login', wait_until='domcontentloaded', timeout=60000)

        email, password = _load_credentials()
        page.fill('input[name="email"]', email)
        page.fill('input[name="password"]', password)

        # Wait for Turnstile to enable the submit button (auto-verifies or user clicks widget)
        print("Waiting for Turnstile to complete (up to 60s — click the checkbox if it appears)...")
        page.wait_for_selector('button[type="submit"]:not([disabled])', timeout=60000)
        page.click('button[type="submit"]')

        print("Waiting for login to complete (up to 60s)...")
        try:
            page.wait_for_function(
                "window.location.pathname !== '/login'",
                timeout=60000
            )
        except Exception:
            pass

        # Wait for the React app to complete the JWT→session exchange internally
        try:
            page.wait_for_timeout(5000)
        except Exception:
            pass
        browser.close()

    if not session_id[0]:
        raise EnvironmentError(
            "Browser login completed but session ID was not captured. "
            "Fall back to manual session: " + SESSION_FILE
        )

    _save_session_to_file(session_id[0])
    print(f"Session saved to {SESSION_FILE}")
    return session_id[0]


def login() -> str:
    session_id = _load_session_from_file()
    if session_id:
        print("Loaded session from file, validating...")
        if _validate_session(session_id):
            print("Session is valid.")
            return session_id
        print("Saved session has expired — re-authenticating via browser.")

    try:
        return _login_via_browser()
    except Exception as e:
        print(f"Browser login failed: {e}")

    print(SESSION_INSTRUCTIONS.format(session_file=SESSION_FILE))
    try:
        raw = input("Or paste a JSESSIONID here and press Enter (leave blank to abort): ").strip()
    except (EOFError, KeyboardInterrupt):
        raw = ""
    if raw:
        _save_session_to_file(raw)
        print(f"Session saved to {SESSION_FILE}")
        return raw
    raise EnvironmentError(
        f"No valid session available. Save a JSESSIONID to: {SESSION_FILE}"
    )


def get_symbol_data(symbol: str, date, prefer_cache: bool, session_id: str) -> PowerGauge:
    if not _SYMBOL_RE.match(symbol):
        raise ValueError(f"Invalid symbol format: {symbol!r}")
    industry_url = f"https://members-backend.chaikinanalytics.com/CPTRestSecure/app/portfolio/getChecklistStocks?symbol={symbol}"
    url = f"https://members-backend.chaikinanalytics.com/CPTRestSecure/app/portfolio/getSymbolData?uid=1101733&symbol={symbol}&components=pgr,metaInfo,EPSData,fundamentalData,technical"
    headers = {'Cookie': f'JSESSIONID={session_id};'}
    pg = PowerGauge(symbol, date)
    data_jsn = {}
    ind_data_jsn = {}

    if date and prefer_cache:
        _base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Data", "Symbol")
        file = os.path.join(_base, symbol, f"{symbol}_{date}.json")
        if not os.path.exists(file):
            file = os.path.join(_base, f"{symbol}_{date}.json")  # flat fallback
        if os.path.exists(file):
            with open(file, "r") as f:
                data_jsn = json.load(f)

    if not data_jsn:
        ind_responce = _get_http_session().get(industry_url, headers=headers, timeout=(5, 20))
        if ind_responce.ok:
            ind_data_jsn = ind_responce.json()
        response = _get_http_session().get(url, headers=headers, timeout=(5, 20))
        if response.ok:
            data_jsn = response.json()
            if ind_data_jsn:
                data_jsn["checklist_stocks"] = ind_data_jsn
            symbol_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Data", "Symbol", symbol)
            os.makedirs(symbol_dir, exist_ok=True)
            cache_date = date if date else datetime.date.today()
            with open(os.path.join(symbol_dir, f"{symbol}_{cache_date}.json"), "w") as fw:
                json.dump(data_jsn, fw)

        elif response.status_code in (401, 403):
            print(SESSION_INSTRUCTIONS.format(session_file=SESSION_FILE))
            raise EnvironmentError(f"Session rejected (HTTP {response.status_code}). Update {SESSION_FILE}.")
        else:
            print(f"Warning: API error for {symbol} (HTTP {response.status_code}) — row will be skipped")
            pg.price = -1
    if data_jsn:
        pg.init_from_json(data_jsn)
        pg.find_prev_pf()
    return pg


def check_from_file(prefer_cache: bool, date=None):
    if date is None:
        date = datetime.date.today()
    elif isinstance(date, datetime.datetime):
        date = date.date()
    _build_cache_index()
    session_id = login()
    print(f"SESSION ID: {session_id}")
    syms_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Data", "symbols_to_check.txt")
    csv_path  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Data", f"symbols_to_check_{date}.csv")
    ohlcv_cache: dict = {}
    with open(syms_path, "r") as f:
        with open(csv_path, "w") as fw:
            for line in f.readlines():
                split_line = line.strip().split()
                symbol = split_line[-1]
                if not _SYMBOL_RE.match(symbol):
                    print(f"  [SKIP] invalid symbol format: {symbol!r}")
                    continue
                symbol_line = f"{split_line[0]},{symbol}"
                power_g = get_symbol_data(symbol, date, prefer_cache, session_id=session_id)

                if symbol not in ohlcv_cache:
                    ohlcv_path = os.path.join(OHLCV_DIR, f"{symbol}_daily.json")
                    try:
                        with open(ohlcv_path) as _f:
                            ohlcv_cache[symbol] = json.load(_f).get('Time Series (Daily)')
                    except FileNotFoundError:
                        ohlcv_cache[symbol] = None
                    except (json.JSONDecodeError, OSError) as e:
                        print(f"  [OHLCV] {symbol}: could not load {ohlcv_path}: {e}")
                        ohlcv_cache[symbol] = None
                ohlcv_ts = ohlcv_cache[symbol]

                f_fields = _compute_pgr_fields(power_g, ohlcv_ts=ohlcv_ts)

                prev_change = power_g.prevPG.change if power_g.prevPG else ""
                percentage_delta = 0
                percentage_delta_plus = 0

                if ohlcv_ts and power_g.pgr_value > 3:
                    all_dates = sorted(ohlcv_ts.keys())
                    date_str = str(date)
                    past = [d for d in all_dates if d <= date_str]
                    if past:
                        idx = all_dates.index(past[-1])
                        prev_count = _ohlcv_streak_count(ohlcv_ts, all_dates, idx - 1, f_fields['prev_percentage']) if idx >= 1 else 0
                        cur_count  = _ohlcv_streak_count(ohlcv_ts, all_dates, idx,     power_g.percentage)
                        if prev_count < 0 and cur_count > 0:
                            percentage_delta = prev_count
                        elif prev_count > 0 and power_g.percentage < 0:
                            percentage_delta = prev_count
                        elif prev_count > 0 and power_g.percentage > 0:
                            percentage_delta_plus = prev_count + 1
                        else:
                            percentage_delta_plus = prev_count - 1

                msg = f"{symbol_line},{power_g.industry_name},{f_fields['prev_pgr']},{f_fields['pgr']},{power_g.industry_strength}," \
                      f"{round(power_g.price*0.95, 2)},{power_g.price},{f_fields['prev_move_price']}," \
                      f"{f_fields['risk_ratio']},{power_g.signals}," \
                      f"{f_fields['prev_percentage']}%,{power_g.percentage}%,{f_fields['prev_move_perc']}%," \
                      f"${prev_change},${power_g.change}," \
                      f"{f_fields['pgr_delta']},{percentage_delta * (-1)},{percentage_delta_plus}," \
                      f"{power_g.lt_trend},{power_g.money_flow},{power_g.over_bt_sl}"

                print(msg)
                fw.write(f"{msg}\n")


# _week_of_month, _compute_seasonality, _predicted_win_pct, _market_regime,
# _rel_volume_bucket, _short_score, _long_score → moved to scoring.py


def _buying_ratio(power_g: PowerGauge, fields: dict) -> float:
    """
    Composite entry-quality score: -10 (strong sell) to +10 (strong buy).

    Components and weights:
      PGR corrected value  ±2.0   (1=Be- → -2, 5=Bu+ → +2)
      Risk/Reward          -1..+2  (rr=0→-1, rr>=3→+2)
      LT trend             ±1.0   (Weak→+1 recovery play, Strong→-1 already extended)
      Money flow           ±0.75  (Strong/Weak)
      OB/OS zone           -0.25..+1.0  (Optimal→+1, Early→+0.25, Wait→-0.25)
      Industry strength    ±0.5   (Weak→+0.5 recovery, Strong→-0.5 extended)
      PGR delta            +0.25  (any change vs yesterday = interesting)
      Seasonality          ±1.0   (week-of-month 10d avg return buckets)

    setup_ok (col U) is display-only: backtesting showed it is a contrarian indicator
    for raw 10d returns (False=+1.36%, True=+0.48%) so it is excluded from the score.
    """
    score = 0.0

    # 1. PGR corrected value (1-5)
    pgr_map = {1: -2.0, 2: -1.0, 3: 0.0, 4: 1.0, 5: 2.0}
    score += pgr_map.get(power_g.pgr_corrected_value, 0.0)

    # 2. Risk/Reward ratio (use raw computed value, not sheet-zeroed value)
    rr = fields.get('risk_ratio', 0)
    if rr >= 3.0:
        score += 2.0
    elif rr >= 2.0:
        score += 1.5
    elif rr >= 1.0:
        score += 1.0
    elif rr >= 0.5:
        score += 0.5
    elif rr > 0:
        score += 0.0
    else:
        score -= 1.0   # no valid stop/target = negative signal

    # 4. Long-term trend
    lt = str(power_g.lt_trend or '').strip()
    lt_map = {'Strong': -1.0, 'Neutral': 0.0, 'Weak': 1.0}
    score += lt_map.get(lt, 0.0)

    # 5. Money flow
    mf = str(power_g.money_flow or '').strip()
    mf_map = {'Strong': 0.75, 'Neutral': 0.0, 'Weak': -0.75}
    score += mf_map.get(mf, 0.0)

    # 6. Overbought/Oversold zone
    ob = str(power_g.over_bt_sl or '').strip()
    ob_map = {'Optimal': 1.0, 'Early': 0.25, 'Neutral': 0.0, 'Wait': -0.25}
    score += ob_map.get(ob, 0.0)

    # 7. Industry strength
    ind = str(power_g.industry_strength or '').strip()
    ind_map = {'Strong': -0.5, 'Weak': 0.5}
    score += ind_map.get(ind, 0.0)

    # 8. PGR delta vs yesterday
    delta = fields.get('pgr_delta', 0)
    score += 0.25 if delta != 0 else 0.0

    # 9. Seasonality
    score += fields.get('seasonality', 0.0)

    return round(max(-10.0, min(10.0, score)), 1)


def _compute_pgr_fields(power_g: PowerGauge, ohlcv_ts: dict = None) -> dict:
    pgr_value = _pgr_str(power_g.pgr_value)
    pgr_corrected_value = _pgr_str(power_g.pgr_corrected_value)
    pgr = pgr_corrected_value if pgr_corrected_value == pgr_value else f"{pgr_corrected_value}/{pgr_value}"
    prev_pgr = 0
    prev_percentage = 0
    pgr_delta = 0
    prev_move_perc = 0
    prev_move_price = 0
    stop_price = 0
    risk_ratio = 0

    if power_g.prevPG:
        prev_pgr_v = _pgr_str(power_g.prevPG.pgr_value)
        prev_pgr_cv = _pgr_str(power_g.prevPG.pgr_corrected_value)
        prev_pgr = prev_pgr_cv if prev_pgr_cv == prev_pgr_v else f"{prev_pgr_cv}/{prev_pgr_v}"
        prev_percentage = power_g.prevPG.percentage
        pgr_delta = power_g.pgr_corrected_value - power_g.prevPG.pgr_corrected_value

    # Stop, target, streak, SMA filter — all from OHLCV (O(lookback), no chain traversal)
    setup_ok = None
    if ohlcv_ts:
        all_dates = sorted(ohlcv_ts.keys())
        date_str = str(power_g.date)
        past = [d for d in all_dates if d <= date_str]
        if past:
            idx = all_dates.index(past[-1])

            # stop: min low of previous _STOP_LOOKBACK_DAYS trading days (excluding today) × 0.99
            stop_w = all_dates[max(0, idx - _STOP_LOOKBACK_DAYS): idx]
            local_low = min((_to_float(ohlcv_ts[d].get('3. low'), 0) for d in stop_w), default=0)
            raw_stop = round(local_low * 0.99, 2) if local_low else 0
            stop_price = raw_stop if raw_stop and raw_stop < power_g.price else 0

            # target: _TARGET_LOOKBACK_DAYS high lookback (excluding today) — matches backtest validation
            tgt_w = all_dates[max(0, idx - _TARGET_LOOKBACK_DAYS): idx]
            if tgt_w:
                hi = max((_to_float(ohlcv_ts[d].get('2. high'), 0) for d in tgt_w), default=0)
                prev_move_price = round(hi, 2) if hi > power_g.price else 0.0

            # risk/reward
            if power_g.price > 0 and stop_price and prev_move_price:
                risk_ratio = round(
                    (prev_move_price - power_g.price) / (power_g.price - stop_price), 2
                )

            # cumulative same-direction streak percentage
            prev_move_perc = _ohlcv_streak_perc(ohlcv_ts, all_dates, idx, power_g.percentage)

            # entry filter: close > SMA(_TREND_SMA_PERIOD) AND close > close[_DIR_CHECK_DAYS ago]
            sma_w = all_dates[max(0, idx - _TREND_SMA_PERIOD): idx]
            if len(sma_w) >= _TREND_SMA_PERIOD:
                sma = sum(_to_float(ohlcv_ts[d].get('4. close'), 0) for d in sma_w) / len(sma_w)
                trend_ok = power_g.price > sma
            else:
                trend_ok = False
            dir_ok = power_g.price > _to_float(ohlcv_ts[all_dates[idx - _DIR_CHECK_DAYS]].get('4. close'), 0) if idx >= _DIR_CHECK_DAYS else False
            setup_ok = trend_ok and dir_ok

    _date_str = str(power_g.date)
    _pattern_score, _pattern_text = _pattern_summary(ohlcv_ts, _date_str)
    _cs  = _cs_score(ohlcv_ts, _date_str) if ohlcv_ts else 0.0
    _cps, _ = _cp_score(ohlcv_ts, _date_str) if ohlcv_ts else (0.0, [])
    _ms,  _ = _mo_score(ohlcv_ts, _date_str) if ohlcv_ts else (0.0, [])

    fields = {
        'pgr': pgr,
        'prev_pgr': prev_pgr,
        'prev_percentage': prev_percentage,
        'pgr_delta': pgr_delta,
        'prev_move_perc': prev_move_perc,
        'prev_move_price': prev_move_price,
        'stop_price': stop_price,
        'risk_ratio': risk_ratio,
        'setup_ok': setup_ok,      # True/False/None
        'seasonality':    _compute_seasonality(ohlcv_ts, power_g.date.month, power_g.date.day),
        'rel_vol':        _rel_volume_bucket(ohlcv_ts, _date_str),
        'market_regime':  _market_regime(_date_str),
        'fibonacci':      _fib_score(ohlcv_ts, _date_str),
        'rsi_divergence': _rsi_div_score(ohlcv_ts, _date_str),
        # Chaikin signal fields needed by scoring.py functions
        'ob_os':           str(power_g.over_bt_sl      or '').strip(),
        'money_flow':      str(power_g.money_flow       or '').strip(),
        'lt_trend':        str(power_g.lt_trend         or '').strip(),
        'industry_strength': str(power_g.industry_strength or '').strip(),
        # Pattern recognition fields
        'candlestick_score': _cs,
        'chart_score':       _cps,
        'momentum_score':    _ms,
        'pattern_score':     _pattern_score,
        'pattern_text':      _pattern_text,
    }
    fields['buying_ratio'] = _buying_ratio(power_g, fields)
    fields['short_score']  = _short_score_fn(fields)
    fields['long_score']   = _long_score_fn(fields)
    return fields


def check_from_xls(prefer_cache: bool, date=None, symbols=None):
    """Update Research sheet from PowerGauge data.

    symbols: optional list/set of ticker strings — process only those rows.
             Pass None (default) to process all rows.
    Fetches are parallelised (_FETCH_WORKERS threads); cell writes remain serial.
    """
    if date is None:
        date = datetime.date.today()
    elif isinstance(date, datetime.datetime):
        date = date.date()
    import openpyxl
    _build_cache_index()
    _orig_backup = _backup_xlsx(XLSX_FILE)
    session_id = login()
    print(f"SESSION ID: {session_id}")

    try:
        # We read from the ROOT folder file
        wb = openpyxl.load_workbook(SRC_XLSX)
    except Exception as e:
        print(f"  [ERROR] Failed to load source {SRC_XLSX}: {e}")
        print(f"  [INFO] Attempting to load existing output {XLSX_FILE} instead...")
        try:
            wb = openpyxl.load_workbook(XLSX_FILE)
        except Exception as e2:
            print(f"  [FATAL] Both source and output files missing or corrupt.")
            return
    
    ws = wb['Research']
    _write_research_headers(ws)

    filter_set = {s.upper() for s in symbols} if symbols else None

    # ── Phase 1: collect valid (symbol, row) pairs in sheet order ────────────
    valid_rows: list[tuple[str, tuple]] = []
    for row in ws.iter_rows(min_row=2, max_col=26):
        symbol = row[3].value
        if not symbol:
            continue
        symbol = str(symbol).strip()
        if not symbol:
            continue
        if filter_set and symbol.upper() not in filter_set:
            continue
        if not _SYMBOL_RE.match(symbol):
            print(f"  [SKIP] invalid symbol format: {symbol!r}")
            continue
        valid_rows.append((symbol, row))

    total = len(valid_rows)
    unique_syms = list(dict.fromkeys(s for s, _ in valid_rows))
    print(f"Fetching {len(unique_syms)} unique symbols ({_FETCH_WORKERS} workers)...")

    # ── Phase 2: parallel fetch ───────────────────────────────────────────────
    pg_results: dict[str, PowerGauge] = {}
    with ThreadPoolExecutor(max_workers=_FETCH_WORKERS) as pool:
        future_to_sym = {
            pool.submit(get_symbol_data, sym, date, prefer_cache, session_id): sym
            for sym in unique_syms
        }
        done = 0
        for future in as_completed(future_to_sym):
            sym = future_to_sym[future]
            done += 1
            try:
                pg_results[sym] = future.result()
            except EnvironmentError:
                pool.shutdown(wait=False, cancel_futures=True)
                raise
            except Exception as e:
                print(f"  [{done}/{len(unique_syms)}] {sym}: fetch error — {e}")
                sentinel = PowerGauge(sym, date)
                sentinel.price = -1
                pg_results[sym] = sentinel
    print(f"Fetch complete ({len(unique_syms)} symbols).")

    # ── Phase 3: serial compute + write ──────────────────────────────────────
    updated = 0
    skipped = 0
    picks_data: list[dict] = []
    ohlcv_cache: dict = {}  # symbol → Time Series dict

    for n, (symbol, row) in enumerate(valid_rows, 1):
        power_g = pg_results[symbol]

        if power_g.price == -1:
            print(f"[{n}/{total}] {symbol}: no market data - row skipped (existing values preserved)")
            skipped += 1
            continue

        # Load OHLCV for entry-filter computation — cached per symbol
        if symbol not in ohlcv_cache:
            ohlcv_path = os.path.join(OHLCV_DIR, f"{symbol}_daily.json")
            try:
                with open(ohlcv_path) as _f:
                    ohlcv_cache[symbol] = json.load(_f).get('Time Series (Daily)')
            except FileNotFoundError:
                ohlcv_cache[symbol] = None
            except (json.JSONDecodeError, OSError) as e:
                print(f"  [OHLCV] {symbol}: could not load {ohlcv_path}: {e}")
                ohlcv_cache[symbol] = None
        ohlcv_ts = ohlcv_cache[symbol]

        f = _compute_pgr_fields(power_g, ohlcv_ts=ohlcv_ts)
        setup_ok = f['setup_ok']   # True / False / None

        row[4].value = power_g.industry_name
        row[5].value = f['prev_pgr']
        row[6].value = f['pgr']
        row[7].value = power_g.industry_strength
        # row[8] col I: manual price level - preserved
        # J=stop, L=target: zero out when filter fails (setup_ok=False)
        row[9].value  = f['stop_price']       if setup_ok is not False else 0  # col J
        row[10].value = power_g.price                                           # col K
        row[11].value = f['prev_move_price']  if setup_ok is not False else 0  # col L
        row[12].value = f['risk_ratio']       if setup_ok is not False else 0  # col M
        row[13].value = f['prev_move_perc']
        row[14].value = f['prev_percentage']
        row[15].value = power_g.percentage
        # row[16] col Q: notes/category - preserved
        row[17].value = power_g.lt_trend
        row[18].value = power_g.money_flow
        row[19].value = power_g.over_bt_sl
        # col U: entry filter flag (1=valid, 0=filtered out, blank=unknown)
        row[20].value = (1 if setup_ok else 0) if setup_ok is not None else None
        # col V: buying ratio -10..+10
        row[21].value = f['buying_ratio']
        # col W: seasonality score for current month (-1..+1)
        row[22].value = f['seasonality'] if f['seasonality'] != 0.0 else None
        # col X: predicted 10d win% from backtest lookup
        row[23].value = _predicted_win_pct(f['buying_ratio'])
        # col Y: short-term 10d entry score
        row[24].value = f['short_score']
        # col Z: long-term 60d position score
        row[25].value = f['long_score']
        # col AA: pattern recognition summary text
        ws.cell(row[0].row, 27).value = f.get('pattern_text') or None

        picks_data.append({
            'symbol':   symbol,
            'industry': power_g.industry_name or '',
            'pgr':      f['pgr'],
            'price':    power_g.price,
            'setup':    (1 if setup_ok else 0) if setup_ok is not None else None,
            'br':       f['buying_ratio'],
            'short10':  f['short_score'],
            'long60':   f['long_score'],
            'ob_os':    str(power_g.over_bt_sl or '').strip(),
            'money_fl': str(power_g.money_flow  or '').strip(),
            'lt_trend': str(power_g.lt_trend    or '').strip(),
            'regime':   f['market_regime'],
            'stop':     f['stop_price'],
            'target':   f['prev_move_price'],
        })

        flag = "OK" if setup_ok else ("--" if setup_ok is False else "??")
        print(f"[{n}/{total}] {symbol}: pgr={f['pgr']}, price={power_g.price}, "
              f"stop={f['stop_price']}, target={f['prev_move_price']}, "
              f"rr={f['risk_ratio']}, setup={flag}, br={f['buying_ratio']}, "
              f"s10={f['short_score']}, l60={f['long_score']}")
        updated += 1

    if picks_data:
        _write_picks_sheet(wb, picks_data, date)

    _touched_sheets = {"Research", "Picks"}
    try:
        import etrade as _et
        _tok = _et.get_tokens("production")
        if _tok:
            _lk   = {p["symbol"]: p for p in picks_data}
            _pos  = _et.fetch_positions(_tok, "production")
            _syms = list({p["symbol"] for p in _pos})
            _qts  = _et.fetch_quotes(_tok, _syms, "production")
            _update_short_long_scores(wb, _lk, _qts, _pos)
            _touched_sheets.add("Short_Long")
            print(f"Short_Long sheet synced: {len(_pos)} positions.")
    except Exception as _e:
        print(f"[E*TRADE] Short_Long skipped: {_e}")

    if picks_data:
        _update_replacements_sheet(wb, picks_data, date.date() if hasattr(date, "date") else date)
        _touched_sheets.add("Replacements")

    try:
        wb.save(XLSX_FILE)
        _fix_comment_shape_ids(XLSX_FILE,
                               original_xlsx=_orig_backup,
                               touched_sheet_names=_touched_sheets)
        print(f"Research sheet updated ({updated} rows written, {skipped} skipped) -> {XLSX_FILE}")
    except PermissionError:
        ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        alt = os.path.join(os.path.dirname(XLSX_FILE), f"investment_pending_{ts}.xlsx")
        wb.save(alt)
        _fix_comment_shape_ids(alt,
                               original_xlsx=_orig_backup,
                               touched_sheet_names=_touched_sheets)
        print(f"ERROR: {XLSX_FILE} is open in another application.")
        print(f"Changes saved to: {alt}")
        print(f"Close Excel and rename/copy that file to state_of_the_day.xlsx")
