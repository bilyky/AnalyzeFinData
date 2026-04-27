import datetime
import requests
import json
import os
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

PGR_STR = ["", "Be-", "Be", "N", "Bu", "Bu+", ""]
power_cookie = ""
abs_path = "C:\\Develop\\StockTrading\\AnalyzeFinData\\"

# Pre-built index of symbol → sorted list of cached JSON paths.
# Populated by _build_cache_index(); find_prev_pf() falls back to glob when empty.
_cache_file_index: dict = {}


def _build_cache_index():
    """Scan Data/Symbol once and build a symbol→[paths] index for find_prev_pf."""
    global _cache_file_index
    symbol_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Data", "Symbol")
    from collections import defaultdict
    idx: dict = defaultdict(list)
    try:
        for entry in os.scandir(symbol_dir):
            if not entry.name.endswith('.json'):
                continue
            sym = entry.name.rsplit('_', 1)[0]
            idx[sym].append(entry.path)
    except OSError:
        pass
    _cache_file_index = {sym: sorted(paths) for sym, paths in idx.items()}
    print(f"Cache index built: {len(_cache_file_index)} symbols")


def _to_float(val, default):
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default

SESSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Data", "session.txt")
_PROXY_URL = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
_PROXIES = {"http": _PROXY_URL, "https": _PROXY_URL} if _PROXY_URL else {}
XLSX_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Data", "investment.xlsx")
XLSX_BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Data", "Backup")
OHLCV_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Data", "Symbol_full")
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
    def __init__(self, symbol, date=datetime.datetime.now().date()):
        self.symbol = symbol
        self.date = date
        self.pgr_value = 0
        self.pgr_corrected_value = 0
        self.industry_name = ""
        self.price = 0.0
        self.max_price = 0.0
        self.signals = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
        self.percentage = 0.0
        self.change = 0.0
        self.prevPG: PowerGauge = None
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
        if _cache_file_index:
            candidates = _cache_file_index.get(self.symbol, [])
        else:
            import glob
            symbol_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Data", "Symbol")
            candidates = sorted(glob.glob(os.path.join(symbol_dir, f"{self.symbol}_*.json")))
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
                self.prevPG = PowerGauge(self.symbol, prev_date)
                with open(path, "r") as f:
                    data_jsn = json.load(f)
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

    def get_prev_same_move_count1(self, days=3, start_price=0):
        pass

    def get_prev_same_move_count(self):
        if not self.prevPG:
            self.find_prev_pf()
        if self.prevPG:
            if self.percentage > 0 and self.prevPG.percentage > 0:
                return self.prevPG.get_prev_same_move_count() + 1
            if self.percentage < 0 and self.prevPG.percentage < 0:
                return self.prevPG.get_prev_same_move_count() - 1
            return -1 if self.percentage < 0 else 1
        return 0

    def get_prev_same_move_percent(self):
        if not self.prevPG:
            self.find_prev_pf()
        if self.prevPG:
            if self.percentage > 0 and self.prevPG.percentage > 0:
                return (self.prevPG.get_prev_same_move_percent() or self.prevPG.percentage) + self.percentage
            if self.percentage < 0 and self.prevPG.percentage < 0:
                return (self.prevPG.get_prev_same_move_percent() or self.prevPG.percentage) + self.percentage
        return 0

    def get_prev_same_move_price(self):
        if not self.prevPG:
            self.find_prev_pf()
        if self.change and self.prevPG and self.prevPG.change:
            if self.change > 0 and self.prevPG.change > 0:
                return self.prevPG.get_prev_same_move_price() or self.prevPG.price
            if self.change < 0 and self.prevPG.change < 0:
                return self.prevPG.get_prev_same_move_price() or self.prevPG.price
        return 0

    def get_prev_max_price(self, cur_price):
        # print(f"cur_price: {cur_price}")
        if not self.prevPG:
            self.find_prev_pf()
        min_pr = self.get_prev_min_of(deep=3)
        self.max_price = max(self.max_price, self.get_prev_max_of(deep=3).price)
        if not min_pr.prevPG:
            min_pr.find_prev_pf()
        if min_pr.prevPG:
            min_pr.max_price = min_pr.prevPG.max_price = self.max_price
            if min_pr.price < cur_price:
                return min_pr.prevPG.get_prev_max_price(cur_price)
            return max(min_pr.get_prev_same_move_price() or min_pr.price, self.max_price)
        return self.max_price

    def get_prev_min_of(self, deep=3):
        if not self.prevPG:
            self.find_prev_pf()
        if self.prevPG:
            self.max_price = max(self.prevPG.max_price, self.max_price)
            # print(f"UUUU: {self.prevPG.max_price}, {self.price} ")
            pr = self
            if deep > 0:
                pr = self.prevPG.get_prev_min_of(deep-1)
            # print(f"MIN: {min(pr.price, self.price)}")
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


def load_date_from_file(date) -> list:
    result = []
    file_name = f"{abs_path}Data\\symbols_to_check_{date}.csv"
    # WMB, N, Oil Gas & Consumable Fuels, 33.62, 000000000000, 2.91%, $0.95
    if not os.path.exists(file_name):
        return result
    with open(file_name, "r") as f:
        for line in f.readlines():
            sym_data = line.split(',')
            symb = PowerGauge(sym_data[0].strip(), date)
            symb.init_from_raw(sym_data)
            result.append(symb)
            break
    return result


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
        r = requests.get(test_url, headers=headers, timeout=15, proxies=_PROXIES, verify=False)
        return r.status_code == 200
    except Exception:
        return False


def _jwt_to_session_id(jwt_token: str) -> str:
    url = ("https://members-backend.chaikinanalytics.com/CPTRestSecure/app"
           "/authenticate/getJWTAuthorization?acquireSessionForcibly=Yes"
           f"&jwtToken={jwt_token}")
    headers = {
        'X-Api-Key': '76J!7fb?jhEtz/hd7i6rHPKklawGZb5VLReDQXa0?4-jGCqQFi74xYCsb0H-hqUC',
        'X-App-Id': 'omni',
    }
    r = requests.get(url, headers=headers, timeout=15, proxies=_PROXIES, verify=False)
    if not r.ok:
        raise EnvironmentError(f"JWT exchange failed: HTTP {r.status_code}")
    session_id = r.json().get('sessionId')
    if not session_id:
        raise EnvironmentError(f"No sessionId in JWT exchange response: {r.text[:200]}")
    return session_id


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

        page.fill('input[name="email"]', 'bilyky@gmail.com')
        page.fill('input[name="password"]', '0605BIYU@')

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


def get_symbol_data(symbol: str, date, from_cache, session_id) -> PowerGauge:

    #     """:authority: 0mlhor0lf8.execute-api.us-east-1.amazonaws.com
    # {email: "bilyky@gmail.com", password: "0605BIYU@"}
    # https://0mlhor0lf8.execute-api.us-east-1.amazonaws.com/prod/login
    industry_url = f"https://app.chaikinanalytics.com/CPTRestSecure/app/portfolio/getChecklistStocks?symbol={symbol}"
    industry_url = f"https://members-backend.chaikinanalytics.com/CPTRestSecure/app/portfolio/getChecklistStocks?symbol={symbol}"
    url = f"https://app.chaikinanalytics.com/CPTRestSecure/app/portfolio/getSymbolData?uid=1101733&symbol={symbol}&components=metaInfo,EPSData,pgr"
    url = f"https://members-backend.chaikinanalytics.com/CPTRestSecure/app/portfolio/getSymbolData?uid=1101733&symbol={symbol}&components=pgr,metaInfo,EPSData,fundamentalData,technical&uid=1101733"
    # https://app.chaikinanalytics.com/login
    payload = {}
    session_id = f'JSESSIONID={session_id};'
    headers = {
      'Cookie': session_id
    }
    pg = PowerGauge(symbol, date)
    data_jsn = {}
    ind_data_jsn = {}

    if date and from_cache:
        file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Data", "Symbol", f"{symbol}_{date}.json")
        if os.path.exists(file):
            with open(file, "r") as f:
                data_jsn = json.load(f)

    if not data_jsn:
        ind_responce = requests.request("GET", industry_url, headers=headers, data=payload, proxies=_PROXIES, verify=False)
        if ind_responce.ok:
            ind_data_jsn = json.loads(ind_responce.text)
        response = requests.request("GET", url, headers=headers, data=payload, proxies=_PROXIES, verify=False)
        if response.ok:
            data_jsn = json.loads(response.text)
            if ind_data_jsn:
                data_jsn["checklist_stocks"] = ind_data_jsn
            symbol_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Data", "Symbol")
            cache_date = date if date else datetime.date.today()
            with open(os.path.join(symbol_dir, f"{symbol}_{cache_date}.json"), "w") as fw:
                json.dump(data_jsn, fw)

        elif response.status_code in (401, 403):
            print(SESSION_INSTRUCTIONS.format(session_file=SESSION_FILE))
            raise EnvironmentError(f"Session rejected (HTTP {response.status_code}). Update {SESSION_FILE}.")
        else:
            print(f"RESP for {url}: {response.status_code} {response.text}")
    if data_jsn:
        pg.init_from_json(data_jsn)
        pg.find_prev_pf()
    return pg


def check_from_file(form_cache, date=datetime.datetime.now()):
    _build_cache_index()
    session_id = login()
    print(f"SESSION ID: {session_id}")
    dd = date
    str_bu = PGR_STR
    syms_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Data", "symbols_to_check.txt")
    csv_path  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Data", f"symbols_to_check_{dd.date()}.csv")
    with open(syms_path, "r") as f:
        with open(csv_path, "w") as fw:
            for line in f.readlines():
                split_line = line.strip().split()
                symbol = split_line[-1]
                symbol_line = f"{split_line[0]},{symbol}"
                power_g = get_symbol_data(symbol, dd.date(), form_cache, session_id=session_id)

                ohlcv_ts = None
                ohlcv_path = os.path.join(OHLCV_DIR, f"{symbol}_daily.json")
                if os.path.exists(ohlcv_path):
                    try:
                        with open(ohlcv_path) as _f:
                            ohlcv_ts = json.load(_f).get('Time Series (Daily)')
                    except Exception:
                        ohlcv_ts = None

                f_fields = _compute_pgr_fields(power_g, ohlcv_ts=ohlcv_ts)

                prev_change = power_g.prevPG.change if power_g.prevPG else ""
                percentage_delta = 0
                percentage_delta_plus = 0

                if ohlcv_ts and power_g.pgr_value > 3:
                    all_dates = sorted(ohlcv_ts.keys())
                    date_str = str(dd.date())
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


def _backup_xlsx():
    import shutil
    now = datetime.datetime.now()
    year_dir = os.path.join(XLSX_BACKUP_DIR, str(now.year))
    os.makedirs(year_dir, exist_ok=True)
    ts = now.strftime("%Y-%m-%d_%H%M%S")
    dst = os.path.join(year_dir, f"investment_{ts}.xlsx")
    shutil.copy2(XLSX_FILE, dst)
    print(f"Backup saved to {dst}")


def _week_of_month(day: int) -> int:
    if day <= 7:  return 1
    if day <= 15: return 2
    if day <= 22: return 3
    return 4


def _compute_seasonality(ohlcv_ts: dict, current_month: int, current_day: int) -> float:
    """
    Historical average 10-day return for the current (month, week-of-month) slot
    across all available years. Week-of-month: 1=days 1-7, 2=8-15, 3=16-22, 4=23+.

    Backtesting showed week-of-month has 2.4x wider win% spread than monthly
    averaging (20pp vs 8.5pp) and improves br>=6 bucket win% from 55.2% to 57.6%.

    Returns a score: +1.0 (strong tailwind) .. -1.0 (headwind), or 0.0 if fewer
    than 3 years of data exist for this slot.
    """
    if not ohlcv_ts:
        return 0.0

    target_wk = _week_of_month(current_day)
    all_dates = sorted(ohlcv_ts.keys())
    date_idx  = {d: i for i, d in enumerate(all_dates)}

    # Last trading date in each (year, month, week) slot
    wk_last = {}
    for d in all_dates:
        y, m, day = int(d[:4]), int(d[5:7]), int(d[8:10])
        w = _week_of_month(day)
        wk_last[(y, m, w)] = d

    returns = []
    for (y, m, w), start_date in wk_last.items():
        if m != current_month or w != target_wk:
            continue
        idx = date_idx[start_date]
        future_idx = idx + 10
        if future_idx >= len(all_dates):
            continue
        c_start  = _to_float(ohlcv_ts[start_date].get('4. close'), 0)
        c_future = _to_float(ohlcv_ts[all_dates[future_idx]].get('4. close'), 0)
        if c_start > 0 and c_future > 0:
            returns.append((c_future - c_start) / c_start * 100)

    if len(returns) < 3:
        return 0.0

    avg = sum(returns) / len(returns)
    if avg > 2.0:   return  1.0
    if avg > 1.0:   return  0.5
    if avg > -1.0:  return  0.0
    if avg > -2.0:  return -0.5
    return -1.0


# Backtested 10d win% by BR bucket (238k obs, 466 symbols, 2023-2025).
# Uses week-of-month seasonality. Monotonically increasing across all 5 buckets.
_WIN_PCT_TABLE = [
    (4.0,  0.643),  # br >= 4
    (2.0,  0.576),  # br 2-4
    (0.0,  0.531),  # br 0-2
    (-2.0, 0.503),  # br -2 to 0
]

def _predicted_win_pct(br: float) -> float:
    for threshold, pct in _WIN_PCT_TABLE:
        if br >= threshold:
            return pct
    return 0.463  # br <= -2


# Column headers and memo text for Research sheet row 1 (cols E–X, 0-indexed 4–23).
# Keys are 0-based column indices. PRESERVE cols A–D (0-3) and I (8) and Q (16).
_RESEARCH_HEADERS = {
    4:  ("Industry",    "Sector/industry name from Chaikin metaInfo."),
    5:  ("Prev PGR",    "Corrected PGR from the most recent prior cache file.\n1=Be-  2=Be  3=N  4=Bu  5=Bu+"),
    6:  ("PGR",         "Current Corrected Power Gauge Rating.\n1=Be-  2=Be  3=Neutral  4=Bu  5=Bu+"),
    7:  ("Ind Strength","Industry group signal from Chaikin.\nStrong / Weak / NA"),
    9:  ("Stop",        "Stop price = min(3-day lows) × 0.99.\nZeroed when entry filter (col U) fails."),
    10: ("Price",       "Last price from Chaikin API."),
    11: ("Target",      "Resistance target = highest 10-day high above current price.\nZeroed when entry filter (col U) fails."),
    12: ("R/R",         "Risk/Reward ratio = (Target − Price) / (Price − Stop).\nZeroed when entry filter (col U) fails."),
    13: ("Prev Move%",  "% price move since the previous Chaikin cache snapshot."),
    14: ("Prev %",      "Day-change% recorded in the previous Chaikin snapshot."),
    15: ("Change%",     "Today's price change% from Chaikin."),
    17: ("LT Trend",    "Long-term price trend from Chaikin.\nStrong / Neutral / Weak\n\nNote: Weak = recovery play (+1 in BR score);\nStrong = already extended (−1 in BR score)."),
    18: ("Money Flow",  "Institutional money flow signal.\nStrong / Neutral / Weak"),
    19: ("OB/OS",       "Overbought / Oversold zone.\nOptimal (+1.0) / Early (+0.25) / Neutral (0) / Wait (−0.25)"),
    20: ("Setup",       "Entry filter: 1 = passed, 0 = failed.\nPass condition: Price > SMA(20) AND Price > Close[3d ago].\nAffects Stop / Target / R/R display only — NOT included in BR score."),
    21: ("BR Score",    "Buying Ratio: composite entry-quality score −10 to +10.\n\nComponents:\n  PGR (1→-2 … 5→+2)\n  R/R (0→-1, ≥0.5→+0.5, ≥1→+1, ≥2→+1.5, ≥3→+2)\n  LT Trend (Weak→+1, Strong→-1)\n  Money Flow (Strong→+0.75, Weak→-0.75)\n  OB/OS (Optimal→+1, Early→+0.25, Wait→-0.25)\n  Industry (Weak→+0.5, Strong→-0.5)\n  PGR Delta (any change→+0.25)\n  Seasonality (−1 to +1)\n\nThresholds: ≥4 strong buy | 2–4 moderate | 0–2 weak | −2–0 avoid | ≤−2 strong avoid"),
    22: ("Seasonal",    "Week-of-month seasonality score (week 1=days 1-7, 2=8-15, 3=16-22, 4=23+).\nDerived from historical 10-day returns for this (month, week) slot across all available years.\n+1.0 = strong tailwind (avg >+2%)   +0.5 = mild tailwind (>+1%)\n 0.0 = neutral                      -0.5 = mild headwind (<-1%)\n-1.0 = strong headwind (avg <-2%)\nRequires >=3 years of OHLCV data; 0 if insufficient.\nNote: 2.4x more predictive than monthly averaging (20pp vs 8.5pp win% spread)."),
    23: ("Win% 10d",    "Predicted 10-day win% from backtest (238k obs, 466 symbols, 2023-2025).\nBased on Buying Ratio (col V) bucket:\n  BR >=  4  -> 64.3%  (strong buy)\n  BR 2-4    -> 57.6%  (moderate)\n  BR 0-2    -> 53.1%  (weak/watch)\n  BR -2-0   -> 50.3%  (neutral/avoid)\n  BR <= -2  -> 46.3%  (avoid)"),
}


def _write_research_headers(ws):
    """Write column labels and cell comments to Research sheet row 1."""
    from openpyxl.comments import Comment
    for col_idx, (label, memo) in _RESEARCH_HEADERS.items():
        cell = ws.cell(row=1, column=col_idx + 1)  # openpyxl is 1-based
        cell.value = label
        cell.comment = Comment(memo, "PowerGauge")


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


def _ohlcv_streak_perc(ohlcv_ts: dict, all_dates: list, idx: int, cur_pct: float) -> float:
    """Sum consecutive same-direction daily % changes ending at idx (from OHLCV closes)."""
    if idx < 1 or cur_pct == 0:
        return round(cur_pct, 4)
    going_up = cur_pct > 0
    total = cur_pct
    for i in range(idx - 1, max(0, idx - 15) - 1, -1):
        prev_close = _to_float(ohlcv_ts[all_dates[i]].get('4. close'), 0)
        curr_close = _to_float(ohlcv_ts[all_dates[i + 1]].get('4. close'), 0)
        if prev_close <= 0 or curr_close <= 0:
            break
        daily_pct = (curr_close - prev_close) / prev_close * 100
        if (daily_pct > 0) == going_up:
            total += daily_pct
        else:
            break
    return round(total, 4)


def _ohlcv_streak_count(ohlcv_ts: dict, all_dates: list, idx: int, cur_pct: float) -> int:
    """Count consecutive same-direction days ending at idx (positive = up-streak, negative = down)."""
    if idx < 1 or cur_pct == 0:
        return 0
    going_up = cur_pct > 0
    count = 1 if going_up else -1
    for i in range(idx - 1, max(0, idx - 30) - 1, -1):
        prev_close = _to_float(ohlcv_ts[all_dates[i]].get('4. close'), 0)
        curr_close = _to_float(ohlcv_ts[all_dates[i + 1]].get('4. close'), 0)
        if prev_close <= 0 or curr_close <= 0:
            break
        daily_pct = (curr_close - prev_close) / prev_close * 100
        if (daily_pct > 0) == going_up:
            count += 1 if going_up else -1
        else:
            break
    return count


def _compute_pgr_fields(power_g: PowerGauge, ohlcv_ts: dict = None) -> dict:
    str_bu = PGR_STR
    pgr_value = str_bu[power_g.pgr_value]
    pgr_corrected_value = str_bu[power_g.pgr_corrected_value]
    pgr = pgr_corrected_value if pgr_corrected_value == pgr_value else f"{pgr_corrected_value}/{pgr_value}"
    prev_pgr = 0
    prev_percentage = 0
    pgr_delta = 0
    prev_move_perc = 0
    prev_move_price = 0
    stop_price = 0
    risk_ratio = 0

    if power_g.prevPG:
        prev_pgr_v = str_bu[power_g.prevPG.pgr_value]
        prev_pgr_cv = str_bu[power_g.prevPG.pgr_corrected_value]
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

            # stop: min low of previous 3 trading days (excluding today) × 0.99
            stop_w = all_dates[max(0, idx - 3): idx]
            local_low = min((_to_float(ohlcv_ts[d].get('3. low'), 0) for d in stop_w), default=0)
            raw_stop = round(local_low * 0.99, 2) if local_low else 0
            stop_price = raw_stop if raw_stop and raw_stop < power_g.price else 0

            # target: 10-day high lookback (excluding today) — matches backtest validation
            tgt_w = all_dates[max(0, idx - 10): idx]
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

            # entry filter: close > SMA(20) AND close > close[3d ago]
            sma_w = all_dates[max(0, idx - 20): idx]
            if len(sma_w) >= 10:
                sma20 = sum(_to_float(ohlcv_ts[d].get('4. close'), 0) for d in sma_w) / len(sma_w)
                trend_ok = power_g.price > sma20
            else:
                trend_ok = False
            dir_ok = power_g.price > _to_float(ohlcv_ts[all_dates[idx - 3]].get('4. close'), 0) if idx >= 3 else False
            setup_ok = trend_ok and dir_ok

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
        'seasonality': _compute_seasonality(ohlcv_ts, power_g.date.month, power_g.date.day),
    }
    fields['buying_ratio'] = _buying_ratio(power_g, fields)
    return fields


def check_from_xls(form_cache, date=datetime.datetime.now(), symbols=None):
    """Update Research sheet from PowerGauge data.

    symbols: optional list/set of ticker strings — process only those rows.
             Pass None (default) to process all rows.
    """
    import openpyxl
    _build_cache_index()
    _backup_xlsx()
    session_id = login()
    print(f"SESSION ID: {session_id}")

    wb = openpyxl.load_workbook(XLSX_FILE)
    ws = wb['Research']
    _write_research_headers(ws)

    filter_set = {s.upper() for s in symbols} if symbols else None
    updated = 0
    skipped = 0

    for row in ws.iter_rows(min_row=2, max_col=24):
        symbol = row[3].value
        if not symbol or not str(symbol).strip():
            continue
        symbol = str(symbol).strip()

        if filter_set and symbol.upper() not in filter_set:
            continue

        power_g = get_symbol_data(symbol, date.date(), form_cache, session_id=session_id)

        if power_g.price == -1:
            print(f"{symbol}: no market data - row skipped (existing values preserved)")
            skipped += 1
            continue

        # Load OHLCV for entry-filter computation (SMA20 + dir3)
        ohlcv_ts = None
        ohlcv_path = os.path.join(OHLCV_DIR, f"{symbol}_daily.json")
        if os.path.exists(ohlcv_path):
            try:
                with open(ohlcv_path) as _f:
                    ohlcv_ts = json.load(_f).get('Time Series (Daily)')
            except Exception:
                ohlcv_ts = None

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

        flag = "OK" if setup_ok else ("--" if setup_ok is False else "??")
        print(f"{symbol}: pgr={f['pgr']}, price={power_g.price}, "
              f"stop={f['stop_price']}, target={f['prev_move_price']}, "
              f"rr={f['risk_ratio']}, setup={flag}, br={f['buying_ratio']}")
        updated += 1

    try:
        wb.save(XLSX_FILE)
        print(f"Research sheet updated ({updated} rows written, {skipped} skipped) -> {XLSX_FILE}")
    except PermissionError:
        ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        alt = os.path.join(os.path.dirname(XLSX_FILE), f"investment_pending_{ts}.xlsx")
        wb.save(alt)
        print(f"ERROR: {XLSX_FILE} is open in another application.")
        print(f"Changes saved to: {alt}")
        print(f"Close Excel and rename/copy that file to investment.xlsx")
