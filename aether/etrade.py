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

from aether import notify
from aether import paths
from aether.config import CFG


_log = logging.getLogger("aether.etrade")

_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Canonical directory for E*TRADE auth-state files (token, browser state, breaker, locks).
# Resolved through aether.paths.data_dir() — the ONE place the $AETHER_DATA_DIR-vs-<checkout>/Data
# rule lives, shared with aether.trash so the token and its soft-delete trash always co-locate.
# Defaults to <checkout>/Data (checkout-relative — the 2026-08-19 "agent couldn't locate the
# token file" incident came from a re-auth in a worktree writing to that worktree's dead Data/);
# set AETHER_DATA_DIR to an absolute path to pin every auth-state file where prod reads.
_DATA_DIR = paths.data_dir()
_TOKEN_PATH = os.path.join(_DATA_DIR, "etrade_tokens.json")
_FAIL_STATE_PATH = os.path.join(_DATA_DIR, "etrade_fail_state.json")

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
# Authoritative auth-probe endpoint. The public market-clock path 404s (it is not a
# valid REST resource on this host), so a clock probe can NEVER return 200 and always
# reads as "inconclusive" even for good tokens. accounts/list is the lightest endpoint
# that actually exercises the OAuth credential and returns 200/401/403 truthfully.
_ACCTLIST_URL = {
    "sandbox":    "https://apisb.etrade.com/v1/accounts/list.json",
    "production": "https://api.etrade.com/v1/accounts/list.json",
}
_BROWSER_STATE_PATH = os.path.join(_DATA_DIR, "etrade_browser_state.json")

# Persistent real-Chrome profile jar for the "one magic button" re-auth engine. Unlike the
# static etrade_browser_state.json SNAPSHOT (a frozen cookie dump that Akamai Bot Manager's
# rolling _abck/bm_sz sensors invalidate the moment JS revalidation is due), a persistent
# profile lets those rolling cookies AND E*TRADE's device-trust live organically in a real
# profile and survive across runs — which is what makes the daily re-auth zero-touch. Under
# _DATA_DIR (gitignored Data/), so the profile — and its cookies — never reach git.
_CHROME_PROFILE_DIR = os.path.join(_DATA_DIR, "etrade_chrome_profile")

# Auth-state files are never hard-deleted in the hot path: a fresh, still-valid token
# can be destroyed by one transient/edge 401, so on rejection/revoke we route through
# the project-wide soft-delete (moved to Data/.trash, recoverable, purged after the
# retention window by the watchdog). See aether/trash.py.
from aether import trash


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
    # Log the ABSOLUTE destination: the token path is checkout-relative, so a re-auth run
    # from the wrong worktree silently saves where prod never reads. Making the target
    # visible turns that class of mistake into something you can see in one glance.
    _log.info(f"E*TRADE {env} token saved ({tokens['issued_date_et']}) -> {_TOKEN_PATH}")


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


def _probe_token_auth(tokens, env="production"):
    """Live-probe cached tokens against E*TRADE's accounts-list endpoint.

    Returns a tri-state so callers can tell a real rejection from a transient blip:
      True  → authorized (HTTP 200)
      False → explicitly rejected by the broker (HTTP 401/403) — safe to invalidate
      None  → indeterminate (404/5xx, rate-limit, network/proxy/timeout) — do NOT
              destroy the token on a transient failure.

    Uses accounts/list (an authenticated GET that truthfully returns 200/401/403) and
    the same proxy/verify settings as every other brokerage call in this module. The
    old market-clock probe hit a path that 404s on this host, so it could never see a
    200 and always reported "inconclusive" even for valid tokens.
    """
    try:
        ck, cs, _, _ = _load_config(env)
        url = _ACCTLIST_URL.get(env, _ACCTLIST_URL["production"])
        session = OAuth1Session(ck, cs, tokens["oauth_token"], tokens["oauth_token_secret"])
        resp = session.get(url, proxies=_proxies(), verify=False, timeout=10)
        if resp.status_code == 200:
            return True
        if resp.status_code in (401, 403):
            return False
        return None
    except Exception:
        return None


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

    lock_path = os.path.join(_DATA_DIR, "etrade_renew.lock")
    renewer = _TokenRenewer(lock_path, _do_renew, lambda: _load_tokens(env),
                            lock_ttl=30, wait_timeout=15)
    return renewer.ensure(current_token=tokens)


def keep_alive(env="production") -> dict | None:
    """Ban-safe session keeper for automated/scheduled contexts (watchdog, cron).

    Renew-ONLY: never opens a browser, never calls _login_headless. It refreshes a
    still-valid same-day token via the OAuth renew endpoint (pure HTTP) and returns it.
    If no valid same-day token exists (e.g. after the nightly midnight-ET expiry or a
    weekend gap), it returns None WITHOUT making any brokerage call — the caller must
    alert a human to re-auth manually (scripts/diagnostics/test_etrade.py). This is the
    correct anti-ban contract: a machine may keep a live session warm, but only a human
    at a browser may create a new one.
    """
    tokens = _load_tokens(env)          # same-day tokens only; None once expired
    if not tokens:
        return None
    if _probe_token_auth(tokens, env) is False:   # broker explicitly rejected (401/403)
        return None
    return renew_tokens(tokens, env)


def revoke_tokens(tokens, env="sandbox") -> bool:
    """Revoke tokens at E*TRADE (e.g. on logout). Returns True on success."""
    ck, cs, _, _ = _load_config(env)
    session = OAuth1Session(ck, cs, tokens["oauth_token"], tokens["oauth_token_secret"])
    try:
        r = session.get(_REVOKE_URL[env], proxies=_proxies(), verify=False, timeout=10)
        if r.ok:
            trash.soft_delete(_TOKEN_PATH, reason="revoked")   # soft-delete (recoverable)
            _log.console("Tokens revoked and cache cleared.")
            return True
        _log.error(f"  [Token] Revoke failed: HTTP {r.status_code}")
        return False
    except Exception as e:
        _log.error(f"  [Token] Revoke error: {e}")
        return False


# ---------------------------------------------------------------------------
# Automated re-auth circuit breaker (anti-ban)
# ---------------------------------------------------------------------------
# Replaying a stale saved browser session from an automation-flagged Chrome trips
# Akamai Bot Manager, which serves a silent spinner-hang instead of the login page.
# Retrying that in a tight loop is exactly what drives E*TRADE to IP-ban us (observed
# 2026-08-18: 4 headless re-auths in 8 min). This breaker is the SINGLE throttle for
# the automated browser path — it escalates the cooldown on every consecutive failure
# and only clears on a SUCCESSFUL login (automated or human). It never blocks the
# human-initiated interactive path; recovery must always be possible.
# Production keeps the canonical filename (back-compat + the file existing test/tooling
# patches point at); every OTHER env gets its own file so a sandbox or test re-auth
# failure can NEVER engage the PRODUCTION breaker — testing stays separated from prod.
_REAUTH_STATE_PATH        = os.path.join(_DATA_DIR, "etrade_reauth_state.json")
_REAUTH_BACKOFF_BASE_MIN  = 15    # first failure → 15 min; doubles each consecutive failure
_REAUTH_BACKOFF_CAP_MIN   = 360   # soft pre-deep cap; at BASE=15 it never actually binds — the
                                  # sequence steps 120→240→480, skipping past 360 (see below)
# Deep-failure ceiling: a sustained streak (>= this many consecutive failures) means the saved
# session is almost certainly dead / the IP is being watched, so from the threshold on the cap
# lifts to a hard 24 h lockout. In practice the observable cooldown just keeps doubling:
# 15→30→60→120→240→480→960, then pins at 1440 (24 h) from the 8th failure. Past a deep streak
# only a human re-auth (which resets the breaker) should bring the automated path back.
_REAUTH_DEEP_FAIL_THRESHOLD = 5
_REAUTH_DEEP_CAP_MIN        = 1440  # 24 h


def _reauth_state_path(env: str = "production") -> str:
    """Per-env circuit-breaker state file. Production uses the canonical path; any other
    env (sandbox/test) is isolated to its own file so its failures cannot cool down the
    production automated-reauth gate. The non-production name is derived from the canonical
    path's directory, so redirecting _REAUTH_STATE_PATH (e.g. to a tempfile in tests)
    relocates every env's state file together."""
    if env == "production":
        return _REAUTH_STATE_PATH
    base, name = os.path.split(_REAUTH_STATE_PATH)
    root, ext  = os.path.splitext(name)
    return os.path.join(base, f"{root}_{env}{ext}")


def _load_reauth_state(env: str = "production") -> dict:
    """Circuit-breaker state: {consecutive_failures, last_attempt, cooldown_until}.

    A missing/corrupt file reads as a fully-open gate (no active cooldown).
    """
    try:
        with open(_reauth_state_path(env)) as f:
            s = json.load(f)
        return {
            "consecutive_failures": int(s.get("consecutive_failures", 0)),
            "last_attempt":         float(s.get("last_attempt", 0.0)),
            "cooldown_until":       float(s.get("cooldown_until", 0.0)),
        }
    except (OSError, ValueError, TypeError):
        return {"consecutive_failures": 0, "last_attempt": 0.0, "cooldown_until": 0.0}


def _save_reauth_state(state: dict, env: str = "production") -> None:
    path = _reauth_state_path(env)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(state, f, indent=2)
    except OSError:
        pass


def _reauth_cooldown_remaining(env: str = "production") -> float:
    """Seconds left on the automated-reauth cooldown. 0.0 means the gate is open."""
    return max(0.0, _load_reauth_state(env)["cooldown_until"] - time.time())


def reset_reauth_circuit_breaker(env: str = "production") -> None:
    """Clear the breaker. Called automatically on any SUCCESSFUL login (including the
    human `scripts/diagnostics/test_etrade.py` path), so a good re-auth restores normal
    automated operation. Safe to call by hand to force a retry."""
    _save_reauth_state({"consecutive_failures": 0, "last_attempt": 0.0, "cooldown_until": 0.0}, env)


def _record_reauth_attempt(env: str = "production") -> None:
    """Pessimistically arm the anti-ban breaker as a browser re-auth STARTS: bump the
    consecutive-failure count and engage the exponential cooldown up front (15→30→60→120→240
    min, lifting to a 24 h ceiling past _REAUTH_DEEP_FAIL_THRESHOLD). A confirmed success then
    retracts it via reset_reauth_circuit_breaker().

    Counting up front rather than after the attempt is the whole fix: the common failure is a
    hang/kill during the browser step (Akamai spinner, then a daemon restart) that never
    returns to record anything, which used to leave the count pinned at 0 so the breaker never
    engaged. Arming first means an attempt that never comes back still backs the next one off.
    """
    state    = _load_reauth_state(env)
    failures = state["consecutive_failures"] + 1
    cap      = _REAUTH_DEEP_CAP_MIN if failures >= _REAUTH_DEEP_FAIL_THRESHOLD else _REAUTH_BACKOFF_CAP_MIN
    backoff  = min(_REAUTH_BACKOFF_BASE_MIN * (2 ** (failures - 1)), cap)
    now = time.time()
    state["consecutive_failures"] = failures
    state["last_attempt"]         = now
    state["cooldown_until"]       = now + backoff * 60
    _save_reauth_state(state, env)
    _log.info(
        f"E*TRADE: automated re-auth attempt #{failures} — breaker armed for {backoff:.0f} min "
        f"(cleared on success). Manual re-auth: python scripts/diagnostics/test_etrade.py {env}"
    )


# ---------------------------------------------------------------------------
# OAuth — automated via Playwright
# ---------------------------------------------------------------------------

def _login_headless(ck: str, cs: str, username: str, password: str, env: str, headless: bool = False) -> dict | None:
    """Fully automatic Playwright login using saved browser state (trusted-device cookies).
    No human interaction required — MFA is skipped by the saved cookies.
    Returns token dict on success, None on failure.

    This is the SINGLE choke point for the automated browser path: the anti-ban circuit
    breaker is enforced here, so no retry loop or second caller can bypass it. The human
    interactive login in get_tokens() does NOT pass through here and is never blocked.
    """
    remaining = _reauth_cooldown_remaining(env)
    if remaining > 0:
        failures = _load_reauth_state(env)["consecutive_failures"]
        _log.error(
            f"E*TRADE: automated re-auth suppressed by circuit breaker "
            f"({remaining / 60:.0f} min remaining, {failures} consecutive failures). "
            f"Manual re-auth: python scripts/diagnostics/test_etrade.py {env}"
        )
        return None

    # Arm the breaker BEFORE the browser (see _record_reauth_attempt): a hang/kill mid-attempt
    # never returns here, so only an up-front arm survives it. Success retracts; any other exit
    # (no verifier, exception, killed process) leaves the arm standing so we back off.
    _record_reauth_attempt(env)
    try:
        oauth = pyetrade.ETradeOAuth(ck, cs)
        auth_url = oauth.get_request_token()
        verifier_code = _get_tokens_via_playwright(
            auth_url, username, password,
            headless=headless,
        )
        if verifier_code:
            tokens = oauth.get_access_token(verifier_code)
            _save_tokens(tokens, env)
            reset_reauth_circuit_breaker(env)   # success retracts the pre-armed failure
            return tokens
    except Exception as e:
        _log.debug(f"_login_headless: {e}")
    return None


def _get_tokens_via_playwright(auth_url, username, password, headless=False):
    """Open the E*TRADE auth URL, log in, accept, and return the verifier code.

    Runs a PERSISTENT real-Chrome profile (_CHROME_PROFILE_DIR) rather than a throwaway
    browser seeded with a static cookie snapshot: the profile dir now IS the state, so
    Akamai's rolling sensor cookies and E*TRADE's device-trust persist across runs and the
    daily re-auth needs no manual OTP. After a successful auth the live cookies are also
    dumped to _BROWSER_STATE_PATH as a harmless secondary backup (the profile is primary).

    (There is no storage_state parameter: launch_persistent_context is incompatible with
    storage_state= — the profile jar supersedes it — so no caller passes a cookie snapshot.)
    headless: run the browser without a window. Default False (headed) for the supervised
    bootstrap / human path; a proven-trusted profile can later drive a headless daily run.
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
        _log.warning(f"  [Auth] Could not auto-fill {step_name} — complete it manually in the browser.")
        return False

    verifier = None

    # Read proxy from config (env vars set by _load_config are not picked up by Playwright)
    proxy_url = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    pw_proxy = {"server": proxy_url} if proxy_url else None

    with sync_playwright() as p:
        # Persistent context: the profile dir carries Akamai's rolling cookies + E*TRADE's
        # device-trust across runs, so there is no separate browser/context to seed or close
        # (launch_persistent_context returns the context and owns the browser).
        os.makedirs(_CHROME_PROFILE_DIR, exist_ok=True)
        _log.info("  [Auth] Using persistent Chrome profile: %s", _CHROME_PROFILE_DIR)
        ctx = p.chromium.launch_persistent_context(
            _CHROME_PROFILE_DIR,
            headless=headless,
            channel="chrome",
            proxy=pw_proxy,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            args=["--disable-blink-features=AutomationControlled"],
        )
        # Remove navigator.webdriver flag so E*TRADE doesn't detect automation
        ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        # Capture verifier from any URL navigation (redirect-based delivery)
        def _on_framenavigated(frame):
            nonlocal verifier
            if frame == page.main_frame and "oauth_verifier=" in frame.url:
                verifier = frame.url.split("oauth_verifier=")[1].split("&")[0]
                _log.info("  [Auth] Verifier captured from redirect: [captured]")

        page.on("framenavigated", _on_framenavigated)

        _SS = _DATA_DIR

        def _snap(name):
            try:
                p = os.path.join(_SS, f"etrade_debug_{name}.png")
                page.screenshot(path=p)
                _log.console(f"  [Debug] Screenshot: {p}  |  URL: {page.url[:80]}")
            except Exception:
                pass

        try:
            _log.console("Opening E*TRADE authorization page...")
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
                        _log.console("  [Auth] Enter pressed on password field.")
                        submitted = True
                        break
                    except Exception:
                        continue
                
                if submitted:
                    # Wait for navigation/load state with grace, without re-submitting and spamming E*TRADE
                    try:
                        page.wait_for_load_state("domcontentloaded", timeout=15000)
                        _log.console("  [Auth] Submitted login form via Enter.")
                    except Exception:
                        _log.warning("  [Auth] Submission page load completed silently or timed out.")
                else:
                    try:
                        page.evaluate("document.querySelector('button').click()")
                        _log.console("  [Auth] Submitted via JS click.")
                        page.wait_for_load_state("domcontentloaded", timeout=15000)
                    except Exception as e:
                        _log.error(f"  [Auth] Submit failed ({e}) — click Log on manually.")
            _snap("03_after_submit")

            # Handle MFA / OTP step (sendotpcode page)
            if "sendotpcode" in page.url or "otp" in page.url.lower():
                _snap("04_otp_page")
                _log.console("  [Auth] MFA required — clicking 'Send Code'...")
                for sel in ["button:has-text('Send Code')", "input[value='Send Code']",
                            "button[type='submit']"]:
                    try:
                        page.click(sel, timeout=5000)
                        _log.console("  [Auth] SMS code sent to your phone.")
                        break
                    except PWTimeout:
                        continue
                _log.console("  [Auth] Enter the SMS code in the browser window, then submit.")
                # Wait up to 2 min for user to leave ALL OTP-related pages
                _otp_pages = ("sendotpcode", "enterotpcode", "verifyotpcode")
                for _ in range(24):
                    page.wait_for_timeout(5000)
                    if not any(p in page.url for p in _otp_pages):
                        _snap("05_after_otp")
                        break
                else:
                    _log.warning("  [Auth] Still on OTP page — complete it manually in the browser.")

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
                    _log.console("  [Auth] Checked agreement checkbox (role).")
                    page.wait_for_timeout(500)
                except Exception:
                    try:
                        page.locator("input[type='checkbox']").first.check(timeout=2000)
                        _log.console("  [Auth] Checked agreement checkbox (locator).")
                        page.wait_for_timeout(500)
                    except Exception:
                        try:
                            page.evaluate(
                                "document.querySelector('input[type=\"checkbox\"]')?.click()"
                            )
                            page.wait_for_timeout(500)
                            _log.console("  [Auth] Checked agreement checkbox (JS).")
                        except Exception:
                            pass

                # Auto-click Accept — try role locator first, then CSS selectors, then JS
                accepted = False
                try:
                    with page.expect_navigation(timeout=15000):
                        page.get_by_role("button", name="Accept").click(timeout=5000)
                    _log.console("  [Auth] Clicked Accept (role).")
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
                            _log.console("  [Auth] Clicked Accept.")
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
                            _log.console("  [Auth] Clicked Accept (JS).")
                            _snap("07_after_accept")
                            accepted = True
                    except Exception:
                        pass

                if not accepted:
                    _snap("07_no_accept")
                    _log.warning("  [Auth] Accept button not found — complete it manually in the browser.")

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
                _log.console("Waiting for E*TRADE verifier code (up to 3 min)...")
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
            _log.error(f"  [Auth] Browser interaction error: {e}")
        finally:
            # Save browser state (trusted-device cookies) before closing.
            # Write via json.dump with utf-8 to avoid Windows cp1252 encoding errors.
            if verifier:
                try:
                    os.makedirs(os.path.dirname(_BROWSER_STATE_PATH), exist_ok=True)
                    state = ctx.storage_state()   # returns dict — no file I/O by Playwright
                    with open(_BROWSER_STATE_PATH, "w", encoding="utf-8") as _f:
                        json.dump(state, _f, indent=2, ensure_ascii=False)
                    _log.console("  [Auth] Browser state saved — future logins skip MFA.")
                except Exception as e:
                    _log.warning(f"  [Auth] Could not save browser state: {e}")
            # Persistent context owns its browser — closing the context tears both down.
            try:
                ctx.close()
            except Exception:
                pass

    # Always fall back to manual entry if automation couldn't capture the verifier
    if not verifier:
        if not sys.stdin.isatty():
            raise RuntimeError(
                "E*TRADE: Cannot prompt for verification code in a headless or non-interactive environment. "
                "Failing immediately to prevent background process hang."
            )

        _log.warning("\nCould not auto-capture verifier. Open this URL in your browser if it isn't open:")
        _log.warning(f"  {auth_url}")
        _log.warning("Log in, click Accept, then paste the code shown on screen.")
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
    fail_path = _FAIL_STATE_PATH
    if os.path.exists(fail_path):
        try:
            with open(fail_path, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"consecutive_failures": 0, "last_failure_time": 0}


def check_etrade_cookie_freshness():
    """Verify that our saved Playwright browser state (cookies) is fresh.
    If it is older than 25 days, send a proactive email warning to re-auth.
    """
    state_path = _BROWSER_STATE_PATH
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
                fail_path = _FAIL_STATE_PATH
                try:
                    os.makedirs(os.path.dirname(fail_path), exist_ok=True)
                    with open(fail_path, "w") as f:
                        json.dump(state, f)
                except Exception:
                    pass

                try:
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


def get_tokens(env="sandbox", allow_browser=False, headless=False):
    """Return valid OAuth tokens, minimising browser interaction.

    Priority:
    1. Today's cached tokens → silent renewal via E*TRADE OAuth endpoint (no browser).
    2. Yesterday's cached tokens → silent renewal attempt.
    3. Silent renewal failed + saved browser state exists → headless Playwright re-auth
       using trusted-device cookies (no MFA, no human needed on most days).
    4. Browser state missing/stale → return None if allow_browser=False,
       or interactive full login if allow_browser=True.

    Return contract (-> dict | None): when allow_browser=False and every silent/headless
    path is exhausted this FAILS SOFT and returns None (it does NOT raise) so guarded
    callers can fall back (e.g. get_live_prices → Google Finance, read_accounts → Excel).
    Callers that use the returned tokens unconditionally MUST guard for None themselves.
    Only the interactive allow_browser=True path can raise (headless-environment guard).
    """
    ck, cs, username, password = _load_config(env)

    # Try today's tokens (fast path — no browser)
    cached = _load_tokens(env)
    if cached:
        # Pre-Flight Active Verification (R&D #21 Unification):
        # We actively test the cached token's validity against E*TRADE's server clock.
        # If it is unauthorized (returns False), we immediately delete the bad token and trigger headless re-auth!
        # If it is authorized (True) or indeterminate (None), we KEEP the token and attempt renewal!
        auth = _probe_token_auth(cached, env)
        if auth is False:
            _log.warning("🚨 [E*TRADE ALERT] Cached token failed server verification (401 Unauthorized). Soft-deleting to trash (recoverable)...")
            trash.soft_delete(_TOKEN_PATH, reason="rejected-401")
            cached = None
        else:
            renewed = renew_tokens(cached, env)
            if renewed:
                check_etrade_cookie_freshness()
                return renewed
        _log.warning("E*TRADE: today's token renewal failed.")

    # Try yesterday's tokens — sessions renewed within 2h survive midnight
    if not cached:
        stale = _load_tokens_any_date(env)
        if stale:
            _log.info("Attempting renewal of previous-day E*TRADE tokens...")
            # Attempt renewal unless the broker explicitly rejects the token (401/403);
            # a transient probe failure (None) should not block the renewal attempt.
            if _probe_token_auth(stale, env) is not False:
                renewed = renew_tokens(stale, env)
                if renewed:
                    _log.info("Previous-day token renewed successfully.")
                    check_etrade_cookie_freshness()
                    return renewed

    # Silent renewal exhausted — try headless Playwright with saved browser state.
    # Uses the same cross-process file lock as renew_tokens() to guarantee only ONE
    # process opens a browser even when multiple tasks fire simultaneously.
    if os.path.exists(_BROWSER_STATE_PATH):
        # Anti-ban circuit breaker (single source of truth = etrade_reauth_state.json,
        # enforced inside _login_headless). Fast-fail here for a clean log + to skip the
        # renewer setup while the breaker is cooling down; _login_headless self-gates too,
        # so this pre-check can never be the only line of defence.
        remaining = _reauth_cooldown_remaining(env)
        if remaining > 0:
            failures = _load_reauth_state(env)["consecutive_failures"]
            _log.error(
                f"E*TRADE: skipping automated re-auth — circuit breaker active "
                f"({remaining / 60:.0f} min remaining, {failures} consecutive failures). "
                f"Manual re-auth: python scripts/diagnostics/test_etrade.py {env}"
            )
        else:
            _log.info("Attempting automatic re-authentication via saved browser state...")
            lock_path = os.path.join(_DATA_DIR, "etrade_reauth.lock")
            reauth_renewer = _TokenRenewer(
                lock_path,
                renew_fn=lambda: _login_headless(ck, cs, username, password, env, headless=headless),
                load_fn=lambda: _load_tokens(env),   # date-checked: only returns today's tokens
                # ttl must exceed the browser's worst case (verifier poll is up to ~3 min +
                # login nav); a shorter ttl lets a second process treat a live winner's lock as
                # stale and launch an overlapping browser (_TokenRenewer._acquire steal path).
                lock_ttl=300,
                wait_timeout=150,
            )
            try:
                tokens = reauth_renewer.ensure(current_token=cached)
                if tokens and tokens.get("issued_date_et") == _et_today():
                    _log.info("E*TRADE: automatic re-authentication succeeded.")
                    return tokens
                _log.warning("E*TRADE: automatic re-authentication failed (browser state may be stale).")
            except Exception as e:
                _log.warning(f"E*TRADE: headless Playwright re-auth error: {e}")

    if not allow_browser:
        # Documented contract (-> dict | None): the headless/automated path fails soft so
        # guarded callers can fall back (e.g. Google Finance pricing). Callers that use the
        # return value unconditionally MUST guard it themselves — not all of them do.
        _log.warning("E*TRADE: all automatic renewal and headless re-auth methods failed; "
                     "returning None. Run scripts/diagnostics/test_etrade.py once to re-authenticate.")
        return None

    if not sys.stdin.isatty():
        raise RuntimeError("E*TRADE: cannot re-authenticate in a headless environment.")

    _log.console("Re-authenticating with browser...")

    oauth = pyetrade.ETradeOAuth(ck, cs)
    auth_url = oauth.get_request_token()
    _log.console(f"Auth URL: {auth_url}")

    verifier_code = _get_tokens_via_playwright(
        auth_url, username, password,
        headless=headless,
    )
    # Redacted: this is captured to a served task-run log by POST /api/etrade/reauth, so log
    # only whether a verifier was captured, never the one-time-use code itself.
    _log.info("Verifier %s", "captured" if verifier_code else "not captured")

    tokens = oauth.get_access_token(verifier_code)
    _save_tokens(tokens, env)
    # A successful human re-auth clears the automated-reauth circuit breaker, so scheduled
    # jobs resume normal operation without waiting out any residual cooldown.
    reset_reauth_circuit_breaker(env)
    _log.console("Tokens saved to cache.")
    return tokens


def _breaker_summary(env: str = "production") -> dict:
    """JSON-friendly snapshot of the automated-reauth breaker for the re-auth result dict."""
    st = _load_reauth_state(env)
    return {
        "consecutive_failures": st["consecutive_failures"],
        "cooldown_remaining_min": round(_reauth_cooldown_remaining(env) / 60, 1),
    }


def reauthenticate(env: str = "production", bootstrap: bool = False, headless: bool = False) -> dict:
    """Human-initiated full E*TRADE re-auth — the single core every front door calls
    (CLI `etrade-login`, POST /api/etrade/reauth, web button).

    Drives the persistent-Chrome-profile engine (_CHROME_PROFILE_DIR): Akamai's rolling
    sensor cookies and E*TRADE's device-trust live in a real profile jar that survives across
    runs, so the daily happy path needs no manual verifier/OTP paste. A rare one-time
    supervised bootstrap re-seeds device trust when it lapses (a human enters the SMS OTP and
    checks "remember this device"); after that the trust window carries the zero-touch runs.

    bootstrap=True forces a headed browser (a human must be present for the OTP). Returns a
    JSON-serializable dict so the HTTP endpoint and CLI render it uniformly. This is a
    HUMAN-initiated action ONLY — the anti-ban contract still forbids scheduled jobs from ever
    opening a browser (they use keep_alive); nothing here is wired into the scheduler.
    """
    if bootstrap:
        headless = False   # a human must watch to enter the one-time OTP that re-seeds trust
    # breaker_state is filled in on every return path below (post get_tokens, so it reflects
    # the attempt); no eager snapshot here — it would just be overwritten.
    result = {
        "ok": False, "env": env, "bootstrap": bootstrap, "issued_date_et": None,
        "has_token": False, "quote_ok": False, "breaker_state": None,
        "message": "",
    }
    try:
        tokens = get_tokens(env, allow_browser=True, headless=headless)
    except Exception as e:
        result["message"] = f"re-auth raised: {e}"
        result["breaker_state"] = _breaker_summary(env)
        return result

    if not tokens:
        result["message"] = ("re-auth failed — no token minted. If the circuit breaker is "
                             "cooling down, wait it out or reset_reauth_circuit_breaker(); "
                             "otherwise re-run supervised with bootstrap=True.")
        result["breaker_state"] = _breaker_summary(env)
        return result

    result["has_token"] = True
    result["issued_date_et"] = tokens.get("issued_date_et")

    # Light live smoke check — a real AAPL quote proves the minted token actually authorizes
    # (mirrors scripts/diagnostics/test_etrade.py). A token that saves but can't quote is a FAIL.
    try:
        market = get_market(tokens, env)
        data = market.get_quote(["AAPL"], resp_format="json")
        quotes = _walk(data, "QuoteData")
        px = _last_trade(quotes[0]) if quotes else 0.0
        result["quote_ok"] = px > 0
        if px > 0:
            result["aapl"] = px
    except Exception as e:
        result["message"] = f"token minted but smoke quote failed: {e}"

    result["breaker_state"] = _breaker_summary(env)
    result["ok"] = result["has_token"] and result["quote_ok"]
    if result["ok"] and not result["message"]:
        result["message"] = f"E*TRADE {env} re-authenticated; token issued {result['issued_date_et']}."
    return result


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


def _last_trade(container, key="All") -> float:
    """lastTrade out of an E*TRADE quote node as a float, 0.0 if missing/blank.

    The price lives under a sub-object — `All` on a market quote, `Quick` on a portfolio
    position — so callers pass the enclosing node and the sub-object key.
    """
    return float((container.get(key) or {}).get("lastTrade", 0) or 0)


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
                px   = _last_trade(pos, "Quick")
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
                px  = _last_trade(q)
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
    tokens = get_tokens()  # -> dict | None (fails soft); use scripts/diagnostics/test_etrade.py to re-auth
    if tokens is None:
        raise SystemExit(
            "E*TRADE tokens unavailable (expired / re-auth needed). "
            "Run: python scripts/diagnostics/test_etrade.py production"
        )

    _log.console("\n--- Quote: AAPL ---")
    market = get_market(tokens)
    _log.console(market.get_quote(["AAPL"], resp_format="json"))

    _log.console("\n--- Accounts ---")
    accts = get_accounts(tokens)
    _log.console(accts.list_accounts(resp_format="json"))
