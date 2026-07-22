"""
AETHER E*TRADE Broker API Session Manager (Singleton).

Coordinates E*TRADE OAuth authentication, token storage, and session keeping.
Enforces a disk-synchronized Singleton architecture: all parallel processes
verify and re-load tokens from disk before calling the renewal endpoints,
completely eliminating duplicate renewals, session collisions, and WAF blocks.
"""

import os
import sys
import json
import time
import datetime
import pyetrade

# Timezone configurations
_ET = datetime.timezone(datetime.timedelta(hours=-4))  # Eastern Time (NY)

_DIR = os.path.dirname(os.path.abspath(__file__))
_TOKEN_PATH = os.path.join(_DIR, "Data", "etrade_tokens.json")

_RENEW_URL = {
    "sandbox":    "https://apisb.etrade.com/oauth/renew_access_token",
    "production": "https://api.etrade.com/oauth/renew_access_token",
}
_REVOKE_URL = {
    "sandbox":    "https://apisb.etrade.com/oauth/revoke_access_token",
    "production": "https://api.etrade.com/oauth/revoke_access_token",
}
_BROWSER_STATE_PATH = os.path.join(_DIR, "Data", "etrade_browser_state.json")


def _et_today() -> str:
    return datetime.datetime.now(_ET).date().isoformat()


# ---------------------------------------------------------------------------
# Config / token helpers
# ---------------------------------------------------------------------------

def _load_config(env="sandbox"):
    from config import CFG
    if env == "sandbox":
        CFG.require("etrade_sandbox_key", "etrade_sandbox_secret")
        ck, cs = CFG.etrade_sandbox_key, CFG.etrade_sandbox_secret
    else:
        CFG.require("etrade_production_key", "etrade_production_secret")
        ck, cs = CFG.etrade_production_key, CFG.etrade_production_secret
    proxy = CFG.etrade_proxy
    if proxy:
        os.environ["HTTPS_PROXY"] = proxy
        os.environ["HTTP_PROXY"]  = proxy
    else:
        os.environ.pop("HTTPS_PROXY", None)
        os.environ.pop("HTTP_PROXY",  None)
    return ck, cs, CFG.etrade_username, CFG.etrade_password


def _proxies():
    p = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    return {"http": p, "https": p} if p else {}


def _save_tokens(tokens, env):
    tokens["env"] = env
    tokens["saved_at"]      = time.time()
    tokens["issued_date_et"] = _et_today()
    os.makedirs(os.path.dirname(_TOKEN_PATH), exist_ok=True)
    with open(_TOKEN_PATH, "w") as f:
        json.dump(tokens, f, indent=2)


def _load_tokens_raw(env) -> dict | None:
    """Load the raw token dictionary currently saved on disk (no date filters)."""
    if not os.path.exists(_TOKEN_PATH):
        return None
    try:
        with open(_TOKEN_PATH, "r") as f:
            tokens = json.load(f)
        if tokens.get("env") == env:
            return tokens
    except Exception:
        pass
    return None


def _load_tokens(env):
    """Return cached tokens if issued today (ET), otherwise None."""
    tokens = _load_tokens_raw(env)
    if not tokens:
        return None
        
    if tokens.get("issued_date_et") != _et_today():
        print("Cached tokens are from a previous trading day — re-authenticating...")
        return None
        
    age_min = (time.time() - tokens.get("saved_at", 0)) / 60
    print(f"Cached tokens found ({age_min:.0f} min old, issued today ET).")
    return tokens


# ---------------------------------------------------------------------------
# Token renewal / revocation
# ---------------------------------------------------------------------------

def renew_tokens(tokens, env="sandbox") -> dict | None:
    """Call E*TRADE renew endpoint. Returns updated tokens, or None if expired.

    Enforces a strict disk-synchronized Singleton check: before making an HTTP
    request to E*TRADE, it force-reloads the token directly from the disk file.
    If another parallel process (like the hourly watchdog keeper) has already
    renewed the token on disk, we instantly inherit and return it, bypassing
    the redundant HTTP renewal completely.
    """
    # 1. Singleton Check: Force-reload from disk to see if another process already renewed it
    disk_tokens = _load_tokens_raw(env)
    if disk_tokens:
        disk_age_min = (time.time() - disk_tokens.get("saved_at", 0)) / 60
        # If the token on disk is valid and was renewed less than 75 minutes ago:
        if disk_age_min < 75 and disk_tokens.get("issued_date_et") == _et_today():
            print(f"  [AETHER Singleton] Token on disk has already been renewed ({disk_age_min:.1f} min old). Reusing without duplicate HTTP renewal.")
            return disk_tokens

    # 2. Proceed with actual HTTP renewal if the disk token is also stale
    from requests_oauthlib import OAuth1Session
    ck, cs, _, _ = _load_config(env)
    session = OAuth1Session(ck, cs, tokens["oauth_token"], tokens["oauth_token_secret"])
    try:
        r = session.get(_RENEW_URL[env], proxies=_proxies(), verify=False, timeout=10)
        if r.ok:
            tokens["saved_at"] = time.time()
            _save_tokens(tokens, env)
            print("Tokens renewed.")
            return tokens
        print(f"  [Token] Renew failed: HTTP {r.status_code} — {r.text[:120]}")
        return None
    except Exception as e:
        print(f"  [Token] Renew error: {e}")
        return None


def revoke_tokens(tokens, env="sandbox"):
    """Call E*TRADE revoke endpoint. Session is permanently destroyed."""
    from requests_oauthlib import OAuth1Session
    ck, cs, _, _ = _load_config(env)
    session = OAuth1Session(ck, cs, tokens["oauth_token"], tokens["oauth_token_secret"])
    try:
        session.get(_REVOKE_URL[env], proxies=_proxies(), verify=False, timeout=10)
        print("Tokens revoked.")
        if os.path.exists(_TOKEN_PATH):
            os.remove(_TOKEN_PATH)
    except Exception as e:
        print(f"Tokens revoke error: {e}")


# ---------------------------------------------------------------------------
# OAuth — automated via Playwright
# ---------------------------------------------------------------------------

def _get_tokens_via_playwright(auth_url, username, password, storage_state=None):
    """
    Launches browser, fills login form, checks for MFA prompt, clicks Accept,
    and returns the 5-letter verifier code from the redirection page.
    """
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    
    # Read proxy from config (env vars set by _load_config are not picked up by Playwright)
    from config import CFG
    proxy = CFG.etrade_proxy
    proxy_arg = {"proxy": {"server": proxy}} if proxy else {}

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,   # headed mode to ensure we bypass Turnstile!
            ignore_https_errors=True,
            **proxy_arg
        )
        
        ctx_kwargs = {
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "viewport": {"width": 1280, "height": 800}
        }
        if storage_state and os.path.exists(storage_state):
            print("  [Auth] Restoring saved browser state (trusted-device cookies).")
            ctx_kwargs["storage_state"] = storage_state
        ctx = browser.new_context(**ctx_kwargs)
        
        # Enable trace or geolocation if required, standard is empty
        page = ctx.new_page()
        
        # Open E*TRADE login page
        print(f"  [Auth] Navigating to: {auth_url}")
        page.goto(auth_url)
        _save_debug_image(page, "01_loaded")
        
        # Check if already authorized (the page redirected directly to verifier, skipping login!)
        if "TradingAPICustomerInfo" in page.url or page.locator("input#agreement").count() > 0 or page.locator("p.verification-code").count() > 0:
            print("  [Auth] Already logged in (session cookie valid). Skipping form submission.")
        else:
            # Type username & password
            try:
                page.locator("input[name='username']").fill(username)
                page.locator("input[name='password']").fill(password)
                _save_debug_image(page, "02_filled")
                
                # Submit form (using standard enter key on password field)
                page.locator("input[name='password']").press("Enter")
                _save_debug_image(page, "03_after_submit")
            except PWTimeout as e:
                print("  [Auth] Could not auto-fill login credentials — complete it manually in the browser.")
                
            # Wait for navigation / MFA check
            # E*TRADE may prompt with security questions or SMS MFA if cookies are expired
            time.sleep(5)
            _save_debug_image(page, "04_after_delay")
            
            # Check for MFA / SMS challenge
            if "challenge" in page.url or page.locator("input#sms-code").count() > 0 or page.locator("text=security code").count() > 0:
                print("\n======================================================================")
                print("👉 ACTION REQUIRED: E*TRADE has presented a security/MFA challenge!")
                print("👉 Please solve the security questions or SMS prompt on your screen now...")
                print("======================================================================\n")
                
                # Wait up to 3 minutes for the user to complete the challenge manually
                try:
                    page.wait_for_url("**/authorize**", timeout=180000)
                    print("  [Auth] Challenge solved! Redirecting to authorization page...")
                except PWTimeout:
                    print("  [Auth] Still on OTP page — complete it manually in the browser.")
                    
        # Check if we are on the authorization / agreement page
        time.sleep(3)
        _save_debug_image(page, "06_authorize_page")
        
        # Click the agreement checkbox and "Accept"
        try:
            # checkbox id is 'agreement'
            agreement_chk = page.locator("input#agreement")
            if agreement_chk.is_visible() and not agreement_chk.is_checked():
                agreement_chk.check()
                print("  [Auth] Checked agreement checkbox.")
            
            # accept button id is 'submitAddress' (or matches text 'Accept')
            accept_btn = page.locator("input#submitAddress, button:has-text('Accept'), input:has-text('Accept')").first
            if accept_btn.is_visible():
                accept_btn.click()
                print("  [Auth] Clicked Accept.")
                time.sleep(5)
                _save_debug_image(page, "07_after_accept")
        except Exception as e:
            print(f"  [Auth] Browser interaction error: {e}")
            
        # Capture the verifier code from the final page
        # The final page typically displays: "Your verification code is: AB12C" inside a <p class="verification-code"> or body text
        verifier = ""
        try:
            code_el = page.locator("p.verification-code, .verification-code, h3:has-text('verification code')")
            if code_el.count() > 0:
                verifier = code_el.first.inner_text().strip()
                # Clean prefix text if present
                if "code" in verifier.lower():
                    verifier = verifier.split(":")[-1].strip()
                    
            if not verifier:
                # Fallback: Parse the full body text for a 5-letter uppercase alphanumeric pattern
                import re
                body_text = page.locator("body").inner_text()
                match = re.search(r"Your verification code is:\s*([A-Z0-9]{5})", body_text, re.IGNORECASE)
                if match:
                    verifier = match.group(1)
        except Exception as e:
            print(f"  [Auth] Failed to parse verifier: {e}")
            
        # Save browser state (trusted-device cookies) before closing.
        # This is the master key to skipping MFA on all future logins!
        try:
            if storage_state:
                os.makedirs(os.path.dirname(storage_state), exist_ok=True)
                ctx.storage_state(path=storage_state)
                print("  [Auth] Browser state saved — future logins skip MFA.")
        except Exception as e:
            print(f"  [Auth] Could not save browser state: {e}")
            
        # Close the browser container
        try:
            context.close()
            browser.close()
        except Exception:
            pass
            
    # If the bot failed to parse the verifier code automatically,
    # gracefully fall back to ask the user to copy/paste it manually!
    if not verifier:
        print(f"\nCould not auto-capture verifier. Open this URL in your browser if it isn't open:")
        print(f"  {auth_url}")
        print("Log in, click Accept, then paste the code shown on screen.")
        verifier = input("Verification code: ").strip()

    return verifier


def _save_debug_image(page, step_name):
    """Save a debug screenshot to the Data/ folder to help troubleshoot login issues."""
    try:
        path = os.path.join(_DIR, "Data", f"etrade_debug_{step_name}.png")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        page.screenshot(path=path)
        print(f"  [Debug] Screenshot: {path}  |  URL: {page.url[:80]}...")
    except Exception:
        pass


def get_tokens(env="sandbox", allow_browser=False):
    """Return valid OAuth tokens, minimising browser interaction.

    Priority order:
    1. Cached tokens from today ET → try renewal (no browser at all).
    2. Cached tokens from today ET but renewal failed → full browser login (only if allow_browser=True).
    3. No cached tokens → full browser login (only if allow_browser=True).
    """
    ck, cs, username, password = _load_config(env)

    cached = _load_tokens(env)
    if cached:
        renewed = renew_tokens(cached, env)
        if renewed:
            return renewed
        print("Token renewal failed.")
        
    # Standard security barrier: If silent renewal failed and browser logins are disabled (default),
    # we MUST abort immediately to prevent session clobbering, watchdog conflicts, and WAF blocks.
    if not allow_browser:
        raise RuntimeError(
            "Critical E*TRADE Failure: Active session token on disk is expired and cannot be renewed. "
            "To prevent background session clobbering, browser-based re-authentication is disabled for standard runs. "
            "Please execute a manual E*TRADE login refresh by running 'python scripts/diagnostics/test_etrade.py'!"
        )

    # Headless safety gate: If running in background/scheduler and renewal fails,
    # we MUST error out immediately instead of spawning Playwright/input() which hangs.
    if not sys.stdin.isatty():
        raise RuntimeError("Critical E*TRADE Failure: Silent token renewal failed in a headless environment. Cannot re-authenticate interactively!")
        
    print("Re-authenticating with browser...")

    oauth = pyetrade.ETradeOAuth(ck, cs)
    auth_url = oauth.get_request_token()

    verifier_code = _get_tokens_via_playwright(
        auth_url, username, password,
        storage_state=_BROWSER_STATE_PATH,
    )
    print(f"Verifier code: {verifier_code}")

    tokens = oauth.get_access_token(verifier_code)
    _save_tokens(tokens, env)
    print("Tokens saved to cache.")
    return tokens


# ---------------------------------------------------------------------------
# Portfolio helpers
# ---------------------------------------------------------------------------

def _walk(d, key):
    """Safely extract a list for `key` from arbitrarily nested dicts."""
    if isinstance(d, dict):
        for k, v in d.items():
            if k == key:
                return v if isinstance(v, list) else [v]
            r = _walk(v, key)
            if r:
                return r
    return []


def fetch_positions(tokens, env="sandbox") -> list[dict]:
    """Return open positions across all accounts as flat dicts."""
    accts = get_accounts(tokens)
    raw   = accts.list_accounts(resp_format="json")
    acct_list = (raw.get("AccountListResponse", {})
                    .get("Accounts", {}).get("Account", []))
    if isinstance(acct_list, dict):
        acct_list = [acct_list]
    out = []
    for acct in acct_list:
        key = acct.get("accountIdKey", "")
        if not key:
            continue
        try:
            port = accts.get_account_portfolio(key, resp_format="json")
        except Exception:
            continue
        for ap in _walk(port, "AccountPortfolio"):
            for pos in _walk(ap, "Position"):
                sym  = pos.get("symbolDescription", "").strip().upper()
                qty  = float(pos.get("quantity",    0) or 0)
                cost = float(pos.get("costPerShare", 0) or 0)
                mval = float(pos.get("marketValue",  0) or 0)
                px   = float((pos.get("Quick") or {}).get("lastTrade", 0) or 0)
                date_ms = int(pos.get("dateAcquired", 0) or 0)
                acq_date = (datetime.datetime
                            .fromtimestamp(date_ms / 1000, tz=datetime.timezone.utc)
                            .date()) if date_ms else None
                acct_id   = acct.get("accountId", "")
                acct_last4 = acct_id[-4:] if len(acct_id) >= 4 else acct_id
                if sym:
                    out.append({
                        "symbol":        sym,
                        "qty":           qty,
                        "cost":          cost,
                        "price":         px or (mval / qty if qty else 0),
                        "mval":          mval,
                        "date_acquired": acq_date,
                        "account_last4": acct_last4,
                    })
    return out


def fetch_quotes(tokens, symbols: list[str], env="sandbox") -> dict[str, float]:
    """Return {SYMBOL: last_price} for the given symbols. Batches to 25 per request."""
    if not symbols:
        return {}
    market = get_market(tokens)
    out: dict[str, float] = {}
    # E*TRADE limits quote requests to 25 symbols per call
    for i in range(0, len(symbols), 25):
        batch = symbols[i:i + 25]
        try:
            data = market.get_quote(batch, resp_format="json")
            for q in _walk(data, "QuoteData"):
                sym = q.get("Product", {}).get("symbol", "").upper()
                px  = float((q.get("All") or {}).get("lastTrade", 0) or 0)
                if sym and px:
                    out[sym] = px
        except Exception:
            continue
    return out


def is_market_open_now(tokens, env="production") -> bool | None:
    """
    Antifragile market status check. Uses a two-factor verification:
    1. Queries the official E*TRADE /v1/market/clock.json API.
    2. Empirically verifies that the SPY ETF has traded today.
    Returns True (Open), False (Closed/Holiday), or None on network failure.
    """
    from requests_oauthlib import OAuth1Session
    import datetime
    import pytz

    ck, cs, _, _ = _load_config(env)
    session = OAuth1Session(ck, cs, tokens["oauth_token"], tokens["oauth_token_secret"])

    try:
        # Factor 1: Official Market Clock API
        clock_url = "https://api.etrade.com/v1/market/clock.json"
        clock_r = session.get(clock_url, verify=False, timeout=10)
        if clock_r.ok:
            clock_data = clock_r.json()
            status = clock_data.get("ClockResponse", {}).get("currentStatus")
            if status and status != "REGULAR":
                return False  # Clock explicitly says closed, pre-market, or after-hours

        # Factor 2: Empirical SPY Quote Timestamp Check
        quote_url = "https://api.etrade.com/v1/market/quote/SPY.json"
        quote_r = session.get(quote_url, verify=False, timeout=10)
        if quote_r.ok:
            quote_data = quote_r.json().get("QuoteResponse", {}).get("QuoteData", [])
            if quote_data:
                dt_utc = quote_data[0].get("dateTimeUTC")
                if dt_utc:
                    trade_time = datetime.datetime.fromtimestamp(dt_utc, pytz.timezone("America/New_York"))    
                    now_ny = datetime.datetime.now(pytz.timezone("America/New_York"))

                    # If the trade did NOT happen today, the market is closed (e.g. holiday)
                    if trade_time.date() != now_ny.date():
                        return False

        # If both checks pass (Clock is REGULAR and SPY traded today), the market is truly open!
        return True
    except Exception:
        return None


def get_market(tokens) -> pyetrade.ETradeMarket:
    ck, cs, _, _ = _load_config(tokens.get("env", "sandbox"))
    return pyetrade.ETradeMarket(ck, cs, tokens["oauth_token"], tokens["oauth_token_secret"])


def get_accounts(tokens) -> pyetrade.ETradeAccounts:
    ck, cs, _, _ = _load_config(tokens.get("env", "sandbox"))
    return pyetrade.ETradeAccounts(ck, cs, tokens["oauth_token"], tokens["oauth_token_secret"])


def get_order(tokens) -> pyetrade.ETradeOrder:
    ck, cs, _, _ = _load_config(tokens.get("env", "sandbox"))
    return pyetrade.ETradeOrder(ck, cs, tokens["oauth_token"], tokens["oauth_token_secret"])
