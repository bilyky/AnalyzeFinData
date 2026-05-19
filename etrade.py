import json
import os
import time
import pyetrade

_DIR = os.path.dirname(__file__)
_CONFIG_PATH = os.path.join(_DIR, "etrade_config.json")
_TOKEN_PATH  = os.path.join(_DIR, "Data", "etrade_tokens.json")


# ---------------------------------------------------------------------------
# Config / token helpers
# ---------------------------------------------------------------------------

def _load_config(env="sandbox"):
    with open(_CONFIG_PATH) as f:
        cfg = json.load(f)
    ck = cfg[env]["consumer_key"]
    cs = cfg[env]["consumer_secret"]
    username = cfg.get("username", "")
    password = cfg.get("password", "")
    proxy = cfg.get("proxy")
    if proxy:
        os.environ["HTTPS_PROXY"] = proxy
        os.environ["HTTP_PROXY"]  = proxy
    else:
        os.environ.pop("HTTPS_PROXY", None)
        os.environ.pop("HTTP_PROXY",  None)
    return ck, cs, username, password


def _save_tokens(tokens, env):
    tokens["env"] = env
    tokens["saved_at"] = time.time()
    os.makedirs(os.path.dirname(_TOKEN_PATH), exist_ok=True)
    with open(_TOKEN_PATH, "w") as f:
        json.dump(tokens, f, indent=2)


def _load_tokens(env):
    """Return cached tokens if they exist and are less than 90 minutes old."""
    if not os.path.exists(_TOKEN_PATH):
        return None
    with open(_TOKEN_PATH) as f:
        tokens = json.load(f)
    if tokens.get("env") != env:
        return None
    age_minutes = (time.time() - tokens.get("saved_at", 0)) / 60
    if age_minutes > 90:
        print(f"Cached tokens expired ({age_minutes:.0f} min old), re-authenticating...")
        return None
    print(f"Using cached tokens ({age_minutes:.0f} min old).")
    return tokens


# ---------------------------------------------------------------------------
# OAuth — automated via Playwright
# ---------------------------------------------------------------------------

def _get_tokens_via_playwright(auth_url, username, password):
    """Open the E*TRADE auth URL, log in, accept, and return the verifier code.

    Tries to auto-fill credentials with several selector variants.
    If auto-fill fails the browser stays open and prompts the user to log in
    manually — the verifier is then captured from the resulting page.
    """
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    _USER_SELECTORS = ["input#USER", "input[name='USER']", "input[name='username']",
                       "input[type='text']", "input[autocomplete='username']"]
    _PASS_SELECTORS = ["input#PASSWORD", "input[name='PASSWORD']",
                       "input[name='password']", "input[type='password']"]
    _ACCEPT_SELECTORS = ["input[value='Accept']", "button[value='Accept']",
                         "input[value='accept']", "a:has-text('Accept')"]
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

    import re as _re
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
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
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

        _SS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Data")

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
                # Press Enter and wait for navigation
                submitted = False
                for sel in _PASS_SELECTORS:
                    try:
                        page.wait_for_selector(sel, timeout=3000)
                        with page.expect_navigation(timeout=15000):
                            page.press(sel, "Enter")
                        print("  [Auth] Submitted via Enter key.")
                        submitted = True
                        break
                    except (PWTimeout, Exception):
                        continue
                if not submitted:
                    try:
                        with page.expect_navigation(timeout=15000):
                            page.evaluate("document.querySelector('button').click()")
                        print("  [Auth] Submitted via JS click.")
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

                # Auto-click Accept/Authorize button
                accepted = False
                for sel in _ACCEPT_SELECTORS:
                    try:
                        page.wait_for_selector(sel, timeout=8000)
                        with page.expect_navigation(timeout=15000):
                            page.click(sel)
                        print("  [Auth] Clicked Accept.")
                        _snap("07_after_accept")
                        accepted = True
                        break
                    except (PWTimeout, Exception):
                        continue
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
        print(f"\nCould not auto-capture verifier. Open this URL in your browser if it isn't open:")
        print(f"  {auth_url}")
        print("Log in, click Accept, then paste the code shown on screen.")
        verifier = input("Verification code: ").strip()

    return verifier


def get_tokens(env="sandbox"):
    """Get OAuth tokens — uses cache if fresh, otherwise runs Playwright login."""
    cached = _load_tokens(env)
    if cached:
        return cached

    ck, cs, username, password = _load_config(env)
    oauth = pyetrade.ETradeOAuth(ck, cs)
    auth_url = oauth.get_request_token()
    print(f"Auth URL: {auth_url}")

    verifier_code = _get_tokens_via_playwright(auth_url, username, password)
    print(f"Verifier code: {verifier_code}")

    tokens = oauth.get_access_token(verifier_code)
    _save_tokens(tokens, env)
    print("Tokens saved to cache.")
    return tokens


# ---------------------------------------------------------------------------
# API object factories
# ---------------------------------------------------------------------------

def get_market(tokens, env="sandbox"):
    ck, cs, _, _ = _load_config(env)
    return pyetrade.ETradeMarket(ck, cs, tokens["oauth_token"], tokens["oauth_token_secret"], dev=(env == "sandbox"))


def get_accounts(tokens, env="sandbox"):
    ck, cs, _, _ = _load_config(env)
    return pyetrade.ETradeAccounts(ck, cs, tokens["oauth_token"], tokens["oauth_token_secret"], dev=(env == "sandbox"))


# ---------------------------------------------------------------------------
# Quick test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tokens = get_tokens()

    print("\n--- Quote: AAPL ---")
    market = get_market(tokens)
    print(market.get_quote(["AAPL"], resp_format="json"))

    print("\n--- Accounts ---")
    accts = get_accounts(tokens)
    print(accts.list_accounts(resp_format="json"))
