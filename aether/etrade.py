import datetime
import json
import logging
import os
import re as _re
import sys
import time
from zoneinfo import ZoneInfo

import pyetrade
import pytz
from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright
from requests_oauthlib import OAuth1Session

from aether.config import CFG


_log = logging.getLogger("aether.etrade")

_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOKEN_PATH = os.path.join(_DIR, "Data", "etrade_tokens.json")

from aether.token_renewer import TokenRenewer as _TokenRenewer


_ET = ZoneInfo("America/New_York")
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
    # Always read the unmocked, physical OS system epoch clock (time.time())
    # to completely isolate live brokerage authentications from any simulated
    # or mocked date-stepping inside your game/backtest runs!
    return datetime.datetime.fromtimestamp(time.time(), _ET).date().isoformat()


# ---------------------------------------------------------------------------
# Config / token helpers
# ---------------------------------------------------------------------------

def _load_config(env="sandbox"):
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


def _load_tokens(env):
    """Return cached tokens if issued today (ET), otherwise None."""
    if not os.path.exists(_TOKEN_PATH):
        return None
    with open(_TOKEN_PATH) as f:
        tokens = json.load(f)
    if tokens.get("env") != env:
        return None
    if tokens.get("issued_date_et") != _et_today():
        _log.info("Cached tokens are from a previous trading day — re-authenticating...")
        return None
    age_min = (time.time() - tokens.get("saved_at", 0)) / 60
    _log.info(f"Cached tokens found ({age_min:.0f} min old, issued today ET).")
    return tokens


def _test_tokens_valid(tokens, env="production") -> bool:
    """Perform a lightweight, 0.1-second live API call to verify if the cached tokens are actively authorized by E*TRADE."""
    try:
        ck, cs, _, _ = _load_config(env)
        # AAPL quote is the standard, 100% active, zero-cost endpoint to verify live token validity
        url = "https://api.etrade.com/v1/market/quote/AAPL.json"
        session = OAuth1Session(ck, cs, tokens["oauth_token"], tokens["oauth_token_secret"])
        resp = session.get(url)
        return resp.status_code == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Token renewal / revocation
# ---------------------------------------------------------------------------

def renew_tokens(tokens, env="sandbox") -> dict | None:
    """Call E*TRADE renew endpoint. Returns updated tokens, or None if expired.

    E*TRADE tokens are valid until midnight ET. Renew extends the session by 2 h.
    Call this before every API session to avoid mid-day expiry.
    """
    # E*TRADE rejects renewal if called too soon (< 55 min) and may revoke the session.
    # We set this to 55 minutes so that hourly (60-minute) Watchdog executions can successfully
    # renew the session without drifting into soft-expiry or triggering E*TRADE rate limits.
    age_min = (time.time() - tokens.get("saved_at", 0)) / 60
    if age_min < 55:
        _log.debug(f"Token {age_min:.0f}m old — reusing without renewal.")
        return tokens

    def _do_renew():
        ck, cs, _, _ = _load_config(env)
        session = OAuth1Session(ck, cs, tokens["oauth_token"], tokens["oauth_token_secret"])
        try:
            r = session.get(_RENEW_URL[env], proxies=_proxies(), verify=False, timeout=10)
            if r.ok:
                tokens["saved_at"] = time.time()
                _save_tokens(tokens, env)
                return tokens
            _log.warning(f"Renew failed: HTTP {r.status_code} — {r.text[:120]}")
        except Exception as e:
            _log.warning(f"Renew error: {e}")
        return None

    lock_path = os.path.join(_DIR, "Data", "etrade_renew.lock")
    renewer = _TokenRenewer(lock_path, _do_renew, lambda: _load_tokens(env),
                            lock_ttl=30, wait_timeout=15)
    return renewer.ensure(current_token=tokens)


def revoke_tokens(tokens, env="sandbox") -> bool:
    """Revoke tokens at E*TRADE (e.g. on logout). Returns True on success."""
    ck, cs, _, _ = _load_config(env)
    session = OAuth1Session(ck, cs, tokens["oauth_token"], tokens["oauth_token_secret"])
    try:
        r = session.get(_REVOKE_URL[env], proxies=_proxies(), verify=False, timeout=10)
        if r.ok:
            if os.path.exists(_TOKEN_PATH):
                os.remove(_TOKEN_PATH)
            print("Tokens revoked and cache cleared.")
            return True
        print(f"  [Token] Revoke failed: HTTP {r.status_code}")
        return False
    except Exception as e:
        print(f"  [Token] Revoke error: {e}")
        return False


# ---------------------------------------------------------------------------
# OAuth — automated via Playwright
# ---------------------------------------------------------------------------

def _login_headless(ck: str, cs: str, username: str, password: str, env: str) -> dict | None:
    """Fully automatic Playwright login using saved browser state (trusted-device cookies).
    No human interaction required — MFA is skipped by the saved cookies.
    Returns token dict on success, None on failure.
    """
    try:
        oauth = pyetrade.ETradeOAuth(ck, cs)
        auth_url = oauth.get_request_token()
        verifier_code = _get_tokens_via_playwright(
            auth_url, username, password,
            storage_state=_BROWSER_STATE_PATH,
        )
        if verifier_code:
            tokens = oauth.get_access_token(verifier_code)
            _save_tokens(tokens, env)
            return tokens
    except Exception as e:
        _log.debug(f"_login_headless: {e}")
    return None


def _get_tokens_via_playwright(auth_url, username, password, storage_state=None):
    """Open the E*TRADE auth URL, log in, accept, and return the verifier code.

    storage_state: path to a saved Playwright browser state (cookies/localStorage).
    When supplied, E*TRADE's trusted-device cookie skips MFA on subsequent runs.
    After a successful auth the browser state is always re-saved to _BROWSER_STATE_PATH.
    """
    _USER_SELECTORS = ["input#USER", "input[name='USER']", "input[name='username']",
                       "input[type='text']", "input[autocomplete='username']"]
    _PASS_SELECTORS = ["input#PASSWORD", "input[name='PASSWORD']",
                       "input[name='password']", "input[type='password']"]
    _ACCEPT_SELECTORS = ["input[value='Accept']", "button[value='Accept']",
                         "input[value='accept']", "button:has-text('Accept')",
                         "a:has-text('Accept')"]
    _VERIFIER_SELECTORS = ["div#oauth_pin", "input#oauth_pin",
                           "div.oauth-pin", "span.verifier", "div.verifier"]

    def _try_fill(page, selectors, value, step_name):
        for sel in selectors:
            try:
                page.wait_for_selector(sel, timeout=4000)
                page.click(sel)
                page.type(sel, value, delay=60)   # human-like keystroke timing
                return True
            except PWTimeout:
                continue
        print(f"  [Auth] Could not auto-fill {step_name} — complete it manually in the browser.")
        return False

    verifier = None

    # Read proxy from config (env vars set by _load_config are not picked up by Playwright)
    proxy_url = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    pw_proxy = {"server": proxy_url} if proxy_url else None

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            channel="chrome",
            proxy=pw_proxy,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx_kwargs = dict(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        if storage_state and os.path.exists(storage_state):
            ctx_kwargs["storage_state"] = storage_state
            print("  [Auth] Restoring saved browser state (trusted-device cookies).")
        ctx = browser.new_context(**ctx_kwargs)
        # Remove navigator.webdriver flag so E*TRADE doesn't detect automation
        ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = ctx.new_page()

        # Capture verifier from any URL navigation (redirect-based delivery)
        def _on_framenavigated(frame):
            nonlocal verifier
            if frame == page.main_frame and "oauth_verifier=" in frame.url:
                verifier = frame.url.split("oauth_verifier=")[1].split("&")[0]
                print(f"  [Auth] Verifier captured from redirect: {verifier}")

        page.on("framenavigated", _on_framenavigated)

        _SS = os.path.join(_DIR, "Data")

        def _snap(name):
            try:
                p = os.path.join(_SS, f"etrade_debug_{name}.png")
                page.screenshot(path=p)
                print(f"  [Debug] Screenshot: {p}  |  URL: {page.url[:80]}")
            except Exception:
                pass

        try:
            print("Opening E*TRADE authorization page...")
            page.goto(auth_url, wait_until="domcontentloaded", timeout=30000)
            _snap("01_loaded")

            # Auto-fill login form
            user_ok = _try_fill(page, _USER_SELECTORS, username, "username")
            pass_ok = _try_fill(page, _PASS_SELECTORS, password, "password")
            _snap("02_filled")
            if user_ok and pass_ok:
                # Press Enter exactly once on the first matching password field to prevent duplicate submission loops
                submitted = False
                for sel in _PASS_SELECTORS:
                    try:
                        page.wait_for_selector(sel, timeout=3000)
                        page.press(sel, "Enter")
                        print("  [Auth] Enter pressed on password field.")
                        submitted = True
                        break
                    except Exception:
                        continue
                
                if submitted:
                    # Wait for navigation/load state with grace, without re-submitting and spamming E*TRADE
                    try:
                        page.wait_for_load_state("domcontentloaded", timeout=15000)
                        print("  [Auth] Submitted login form via Enter.")
                    except Exception:
                        print("  [Auth] Submission page load completed silently or timed out.")
                else:
                    try:
                        page.evaluate("document.querySelector('button').click()")
                        print("  [Auth] Submitted via JS click.")
                        page.wait_for_load_state("domcontentloaded", timeout=15000)
                    except Exception as e:
                        print(f"  [Auth] Submit failed ({e}) — click Log on manually.")
            _snap("03_after_submit")

            # Handle MFA / OTP step (sendotpcode page)
            if "sendotpcode" in page.url or "otp" in page.url.lower():
                _snap("04_otp_page")
                print("  [Auth] MFA required — clicking 'Send Code'...")
                for sel in ["button:has-text('Send Code')", "input[value='Send Code']",
                            "button[type='submit']"]:
                    try:
                        page.click(sel, timeout=5000)
                        print("  [Auth] SMS code sent to your phone.")
                        break
                    except PWTimeout:
                        continue
                print("  [Auth] Enter the SMS code in the browser window, then submit.")
                # Wait up to 2 min for user to leave ALL OTP-related pages
                _otp_pages = ("sendotpcode", "enterotpcode", "verifyotpcode")
                for _ in range(24):
                    page.wait_for_timeout(5000)
                    if not any(p in page.url for p in _otp_pages):
                        _snap("05_after_otp")
                        break
                else:
                    print("  [Auth] Still on OTP page — complete it manually in the browser.")

            if not verifier:
                # Wait for the authorize page to fully load
                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass
                _snap("06_authorize_page")

                # Scroll window + any scrollable divs to reveal checkbox/buttons
                try:
                    page.evaluate("""
                        window.scrollTo(0, document.body.scrollHeight);
                        Array.from(document.querySelectorAll('div')).forEach(d => {
                            if (d.scrollHeight > d.clientHeight) d.scrollTop = d.scrollHeight;
                        });
                    """)
                    page.wait_for_timeout(700)
                except Exception:
                    pass

                # Check agreement checkbox — try role locator, CSS, then JS fallback
                try:
                    page.get_by_role("checkbox").first.check(timeout=3000)
                    print("  [Auth] Checked agreement checkbox (role).")
                    page.wait_for_timeout(500)
                except Exception:
                    try:
                        page.locator("input[type='checkbox']").first.check(timeout=2000)
                        print("  [Auth] Checked agreement checkbox (locator).")
                        page.wait_for_timeout(500)
                    except Exception:
                        try:
                            page.evaluate(
                                "document.querySelector('input[type=\"checkbox\"]')?.click()"
                            )
                            page.wait_for_timeout(500)
                            print("  [Auth] Checked agreement checkbox (JS).")
                        except Exception:
                            pass

                # Auto-click Accept — try role locator first, then CSS selectors, then JS
                accepted = False
                try:
                    with page.expect_navigation(timeout=15000):
                        page.get_by_role("button", name="Accept").click(timeout=5000)
                    print("  [Auth] Clicked Accept (role).")
                    _snap("07_after_accept")
                    accepted = True
                except Exception:
                    pass

                if not accepted:
                    for sel in _ACCEPT_SELECTORS:
                        try:
                            page.wait_for_selector(sel, timeout=5000)
                            with page.expect_navigation(timeout=15000):
                                page.click(sel)
                            print("  [Auth] Clicked Accept.")
                            _snap("07_after_accept")
                            accepted = True
                            break
                        except (PWTimeout, Exception):
                            continue

                if not accepted:
                    # JS fallback — click any button whose visible text is "Accept"
                    try:
                        _url_before = page.url
                        page.evaluate("""() => {
                            const btns = [...document.querySelectorAll('button, input[type=submit]')];
                            const a = btns.find(b => (b.textContent || b.value || '').trim() === 'Accept');
                            if (a) a.click();
                        }""")
                        page.wait_for_timeout(3000)
                        if page.url != _url_before:
                            print("  [Auth] Clicked Accept (JS).")
                            _snap("07_after_accept")
                            accepted = True
                    except Exception:
                        pass

                if not accepted:
                    _snap("07_no_accept")
                    print("  [Auth] Accept button not found — complete it manually in the browser.")

            def _try_read_verifier():
                """Attempt to extract verifier from the current page."""
                # 1. E*TRADE puts the code in a readonly/text input on the Complete Authorization page
                for sel in ["input[readonly]", "input#oauth_pin", "input[name='oauth_verifier']",
                            "input[type='text']"]:
                    try:
                        el = page.query_selector(sel)
                        if el:
                            val = (el.get_attribute("value") or "").strip()
                            if val and _re.match(r'^[A-Z0-9]{4,10}$', val):
                                return val
                    except Exception:
                        pass
                # 2. Known text containers
                for sel in _VERIFIER_SELECTORS:
                    try:
                        el = page.query_selector(sel)
                        if el:
                            text = el.inner_text().strip()
                            if text and _re.match(r'^[A-Z0-9]{4,10}$', text):
                                return text
                    except Exception:
                        pass
                # 3. Scan body — look for "verification code" context then grab adjacent uppercase word
                try:
                    body = page.inner_text("body")
                    # Match the code that appears right after "verification code is below" or similar
                    m = _re.search(r'(?:verification code[^A-Z0-9]*|code is[^A-Z0-9]*)([A-Z0-9]{4,10})', body)
                    if m:
                        return m.group(1)
                except Exception:
                    pass
                return None

            # Poll page for verifier up to 3 minutes
            if not verifier:
                print("Waiting for E*TRADE verifier code (up to 3 min)...")
                for _ in range(36):
                    if verifier:
                        break
                    try:
                        verifier = _try_read_verifier()
                    except Exception:
                        pass
                    if verifier:
                        break
                    page.wait_for_timeout(5000)

        except Exception as e:
            print(f"  [Auth] Browser interaction error: {e}")
        finally:
            # Save browser state (trusted-device cookies) before closing.
            # Write via json.dump with utf-8 to avoid Windows cp1252 encoding errors.
            if verifier:
                try:
                    os.makedirs(os.path.dirname(_BROWSER_STATE_PATH), exist_ok=True)
                    state = ctx.storage_state()   # returns dict — no file I/O by Playwright
                    with open(_BROWSER_STATE_PATH, "w", encoding="utf-8") as _f:
                        json.dump(state, _f, indent=2, ensure_ascii=False)
                    print("  [Auth] Browser state saved — future logins skip MFA.")
                except Exception as e:
                    print(f"  [Auth] Could not save browser state: {e}")
            try:
                ctx.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass

    # Always fall back to manual entry if automation couldn't capture the verifier
    if not verifier:
        if not sys.stdin.isatty():
            raise RuntimeError(
                "E*TRADE: Cannot prompt for verification code in a headless or non-interactive environment. "
                "Failing immediately to prevent background process hang."
            )

        print("\nCould not auto-capture verifier. Open this URL in your browser if it isn't open:")
        print(f"  {auth_url}")
        print("Log in, click Accept, then paste the code shown on screen.")
        try:
            verifier = input("Verification code: ").strip()
        except EOFError:
            raise RuntimeError(
                "E*TRADE: Cannot prompt for verification code in a headless or non-interactive environment (EOFError). "
                "Failing immediately to prevent background process hang."
            )

    return verifier


def _load_tokens_any_date(env) -> dict | None:
    """Load cached tokens regardless of issue date — for renewal attempts."""
    if not os.path.exists(_TOKEN_PATH):
        return None
    try:
        with open(_TOKEN_PATH) as f:
            tokens = json.load(f)
        return tokens if tokens.get("env") == env else None
    except Exception:
        return None


def _get_failure_state():
    fail_path = os.path.join(_DIR, "Data", "etrade_fail_state.json")
    if os.path.exists(fail_path):
        try:
            with open(fail_path, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"consecutive_failures": 0, "last_failure_time": 0}


def _reset_failure_count():
    fail_path = os.path.join(_DIR, "Data", "etrade_fail_state.json")
    try:
        state = _get_failure_state()
        if state["consecutive_failures"] > 0:
            state["consecutive_failures"] = 0
            os.makedirs(os.path.dirname(fail_path), exist_ok=True)
            with open(fail_path, "w") as f:
                json.dump(state, f)
    except Exception:
        pass


def _increment_failure_count():
    fail_path = os.path.join(_DIR, "Data", "etrade_fail_state.json")
    state = _get_failure_state()
    state["consecutive_failures"] += 1
    state["last_failure_time"] = time.time()
    try:
        os.makedirs(os.path.dirname(fail_path), exist_ok=True)
        with open(fail_path, "w") as f:
            json.dump(state, f)
    except Exception:
        pass


def check_etrade_cookie_freshness():
    """Verify that our saved Playwright browser state (cookies) is fresh.
    If it is older than 25 days, send a proactive email warning to re-auth.
    """
    state_path = os.path.join(_DIR, "Data", "etrade_browser_state.json")
    if os.path.exists(state_path):
        try:
            mtime = os.path.getmtime(state_path)
            age_days = (time.time() - mtime) / (24 * 3600)
            if age_days > 25.0:
                _log.warning(f"🕒 [E*TRADE ALERT] Your E*TRADE Trusted Device cookies are {age_days:.1f} days old and expiring soon!")
                
                # Deduplicate email alerts: send at most once per calendar day
                state = _get_failure_state()
                today_str = _et_today()
                if state.get("last_warning_sent_date") == today_str:
                    return
                
                state["last_warning_sent_date"] = today_str
                fail_path = os.path.join(_DIR, "Data", "etrade_fail_state.json")
                try:
                    os.makedirs(os.path.dirname(fail_path), exist_ok=True)
                    with open(fail_path, "w") as f:
                        json.dump(state, f)
                except Exception:
                    pass

                try:
                    from aether import notify
                    msg = (
                        f"🕒 [PROACTIVE MAINTENANCE NOTICE]\n\n"
                        f"Your saved E*TRADE Trusted Device cookies are {age_days:.1f} days old (expiration threshold is 30-45 days).\n"
                        f"To prevent an automatic daily run blockage or offline lockout during active trading hours, "
                        f"please execute a manual re-authentication sync to refresh your trusted-device session state:\n\n"
                        f"  powershell command:\n"
                        f"  python -c \"import aether.etrade as e; e.get_tokens('production', allow_browser=True)\"\n\n"
                        f"Please complete this sync during after-hours."
                    )
                    notify.send_email("🕒 ALERT: E*TRADE Trusted Session Expiring Soon", msg)
                    _log.info("📧 Proactive E*TRADE session expiration warning dispatched via email.")
                except Exception as ne:
                    _log.error(f"Failed to dispatch proactive session warning email: {ne}")
        except Exception as e:
            _log.error(f"Error checking E*TRADE cookie freshness: {e}")


def get_tokens(env="sandbox", allow_browser=False):
    """Return valid OAuth tokens, minimising browser interaction.

    Priority:
    1. Today's cached tokens → silent renewal via E*TRADE OAuth endpoint (no browser).
    2. Yesterday's cached tokens → silent renewal attempt.
    3. Silent renewal failed + saved browser state exists → headless Playwright re-auth
       using trusted-device cookies (no MFA, no human needed on most days).
    4. Browser state missing/stale → return None if allow_browser=False,
       or interactive full login if allow_browser=True.
    """
    ck, cs, username, password = _load_config(env)

    # Try today's tokens (fast path — no browser)
    cached = _load_tokens(env)
    if cached:
        # Pre-Flight Active Verification (R&D #21 Unification):
        # We actively test the cached token's validity against E*TRADE's server clock.
        # If it is unauthorized, we immediately delete the bad token and trigger headless re-auth!
        if _test_tokens_valid(cached, env):
            renewed = renew_tokens(cached, env)
            if renewed:
                check_etrade_cookie_freshness()
                return renewed
        else:
            _log.warning("🚨 [E*TRADE ALERT] Cached token failed server verification (401 Unauthorized). Invalidating cache...")
            try:
                os.remove(_TOKEN_PATH)
            except Exception:
                pass
            cached = None
        _log.warning("E*TRADE: today's token renewal failed.")

    # Try yesterday's tokens — sessions renewed within 2h survive midnight
    if not cached:
        stale = _load_tokens_any_date(env)
        if stale:
            _log.info("Attempting renewal of previous-day E*TRADE tokens...")
            # Check if stale is valid first
            if _test_tokens_valid(stale, env):
                renewed = renew_tokens(stale, env)
                if renewed:
                    _log.info("Previous-day token renewed successfully.")
                    check_etrade_cookie_freshness()
                    return renewed

    # Silent renewal exhausted — try headless Playwright with saved browser state.
    # Uses the same cross-process file lock as renew_tokens() to guarantee only ONE
    # process opens a browser even when multiple tasks fire simultaneously.
    if os.path.exists(_BROWSER_STATE_PATH):
        _log.info("Attempting automatic re-authentication via saved browser state...")

        def _do_headless_login_with_cooldown():
            # ── Headless Re-auth Cooldown Circuit Breaker (Anti-Ban Guard) ──
            # Run the cooldown check and touch INSIDE the cross-process lock to prevent race conditions
            cooldown_path = os.path.join(_DIR, "Data", "etrade_cooldown.lock")
            if os.path.exists(cooldown_path):
                try:
                    mtime = os.path.getmtime(cooldown_path)
                    cooldown_age_min = (time.time() - mtime) / 60
                    
                    # Calculate dynamic cooldown duration based on consecutive failure count
                    fail_state = _get_failure_state()
                    fails = fail_state.get("consecutive_failures", 0)
                    if fails >= 3:
                        cooldown_duration_min = 1440.0  # 24 hours backoff (1 day)
                        msg_str = f"Critical consecutive failure lockout (24h backoff active, {fails} failures)."
                    elif fails >= 2:
                        cooldown_duration_min = 120.0   # 2 hours backoff
                        msg_str = f"Moderate failure backoff (2h backoff active, {fails} failures)."
                    else:
                        cooldown_duration_min = 15.0    # 15 minutes default
                        msg_str = "Headless re-authentication is on cooldown."

                    if cooldown_age_min < cooldown_duration_min:
                        remaining_min = cooldown_duration_min - cooldown_age_min
                        _log.warning(f"❌ [E*TRADE ANTI-BAN BLOCK] {msg_str} ({remaining_min:.1f}m/{cooldown_duration_min:.0f}m remaining). Aborting browser launch to prevent brokerage bans!")
                        raise RuntimeError("E*TRADE: Headless auto-login blocked by Anti-Ban Cooldown Circuit Breaker.")
                except Exception as ce:
                    if "Anti-Ban" in str(ce):
                        raise ce

            # Touch/create the cooldown lock file immediately before launching the browser!
            try:
                os.makedirs(os.path.dirname(cooldown_path), exist_ok=True)
                with open(cooldown_path, "w") as cf:
                    cf.write(str(time.time()))
            except Exception:
                pass

            return _login_headless(ck, cs, username, password, env)

        lock_path = os.path.join(_DIR, "Data", "etrade_reauth.lock")
        reauth_renewer = _TokenRenewer(
            lock_path,
            renew_fn=_do_headless_login_with_cooldown,
            load_fn=lambda: _load_tokens(env),   # date-checked: only returns today's tokens
            lock_ttl=120,   # browser login can take up to 2 min
            wait_timeout=150,
        )
        try:
            tokens = reauth_renewer.ensure(current_token=cached)
            if tokens and tokens.get("issued_date_et") == _et_today():
                _log.info("E*TRADE: automatic re-authentication succeeded.")
                _reset_failure_count()
                return tokens
            _log.warning("E*TRADE: automatic re-authentication failed (browser state may be stale).")
            _increment_failure_count()
        except Exception as e:
            if "Anti-Ban" in str(e) or "cooldown" in str(e).lower():
                raise e
            _log.warning(f"E*TRADE: headless Playwright re-auth error: {e}")
            _increment_failure_count()

    if not allow_browser:
        # FAIL LOUD AND SCREAM: Throw a clear, un-swallowed RuntimeError to trigger the AETHER Healer!
        raise RuntimeError("🚨 [CRITICAL E*TRADE FAILURE] All automatic renewal and headless re-authentication methods failed! Your live E*TRADE feed is unauthorized/offline.")

    if not sys.stdin.isatty():
        raise RuntimeError("E*TRADE: cannot re-authenticate in a headless environment.")

    print("Re-authenticating with browser...")

    oauth = pyetrade.ETradeOAuth(ck, cs)
    auth_url = oauth.get_request_token()
    print(f"Auth URL: {auth_url}")

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
    accts = get_accounts(tokens, env)
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
    market = get_market(tokens, env)
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


# ---------------------------------------------------------------------------
# API object factories
# ---------------------------------------------------------------------------

def get_market(tokens, env=None):
    if env is None:
        env = tokens.get("env", "sandbox")
    ck, cs, _, _ = _load_config(env)
    return pyetrade.ETradeMarket(ck, cs, tokens["oauth_token"], tokens["oauth_token_secret"], dev=(env == "sandbox"))


def get_accounts(tokens, env=None):
    if env is None:
        env = tokens.get("env", "sandbox")
    ck, cs, _, _ = _load_config(env)
    return pyetrade.ETradeAccounts(ck, cs, tokens["oauth_token"], tokens["oauth_token_secret"], dev=(env == "sandbox"))


def is_market_open_now(tokens, env="production") -> bool | None:
    """
    Antifragile market status check. Uses a two-factor verification:
    1. Queries the official E*TRADE /v1/market/clock.json API.
    2. Empirically verifies that the SPY ETF has traded today.
    Returns True (Open), False (Closed/Holiday), or None on network failure.
    """
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


# ---------------------------------------------------------------------------
# Quick test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tokens = get_tokens()  # raises if tokens expired; use scripts/diagnostics/test_etrade.py to re-auth

    print("\n--- Quote: AAPL ---")
    market = get_market(tokens)
    print(market.get_quote(["AAPL"], resp_format="json"))

    print("\n--- Accounts ---")
    accts = get_accounts(tokens)
    print(accts.list_accounts(resp_format="json"))
