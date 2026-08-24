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


class SmsRequired(Exception):
    """Raised when an AUTOMATED (headless) re-auth reaches E*TRADE's SMS/OTP wall.

    Device-trust has lapsed, so E*TRADE demands a one-time SMS code no code can supply. This
    is NOT a failure/ban condition — the browser and login worked, trust merely expired — so
    the automated door (scheduled_reauth) catches it to latch the profile 'sms_required' and
    alert a human, WITHOUT escalating the anti-ban circuit breaker. Carries the env for logs.
    """
    def __init__(self, env: str = "production"):
        super().__init__(f"E*TRADE {env}: SMS/OTP required — device trust lapsed")
        self.env = env


# Checkout root. This module lives at <root>/aether/etrade/__init__.py, so climb THREE
# levels (etrade/ -> aether/ -> root). Was two when this was the single-file aether/etrade.py.
_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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

# Trust-state marker driving the automated daily re-auth door (scheduled_reauth). The state
# machine is UNSEEDED → TRUSTED → SMS_REQUIRED: the automated path opens a browser ONLY when
# this reads "trusted" (a supervised bootstrap proved the profile holds device-trust), and
# latches to "sms_required" the instant a headless run hits the OTP wall — so no automated
# browser ever hammers the login while device-trust is down. Gitignored Data/, never in git.
_TRUST_MARKER_PATH = os.path.join(_DATA_DIR, "etrade_profile_trusted.json")

# Single-source JS predicate: "has the browser LEFT the login page?" — true once we've reached
# the OAuth consent page, the OTP wall, or any non-login URL. Used by both the headed (human)
# and headless (auto-submit) waits below so the detection rule can't drift between them.
_LEFT_LOGIN_PAGE_JS = """() => {
    const href = location.href.toLowerCase();
    if (!href.includes('/pxy/login')) return true;   // left the login page
    if (href.includes('otp')) return true;           // OTP wall reached
    return !!document.querySelector(
        "input[value='Accept'], button[value='Accept'], #oauth_pin");
}"""

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


def _et_now() -> str:
    """Full ET timestamp (seconds) — stamps WHEN an auth verdict was computed (Temporal
    Zero-Trust: a status is only true as of its check time). Same unmocked physical clock."""
    return datetime.datetime.fromtimestamp(time.time(), _ET).isoformat(timespec="seconds")


class AuthReason:
    """The E*TRADE auth-state vocabulary, defined once.

    Shared by auth_status() and scheduled_reauth() so neither hardcodes a divergent string. The
    first six values double as scheduled_reauth's `reason` field (string values unchanged), so
    both paths speak one language.
    """
    RENEWED       = "renewed"        # a live same-day token was refreshed via ban-free HTTP renew
    REAUTHED      = "reauthed"       # a fresh token was minted through the browser (automated door)
    SMS_REQUIRED  = "sms_required"   # device trust lapsed — a human SMS bootstrap is required
    UNSEEDED      = "unseeded"       # profile never bootstrapped — a supervised bootstrap is required
    BREAKER       = "breaker"        # the anti-ban circuit breaker is cooling down
    FAILED        = "failed"         # an automated browser re-auth attempt failed
    LIVE          = "live"           # token present and valid (local record, or probe-confirmed)
    EXPIRED       = "expired"        # token dead (overnight/midnight-ET) or broker-rejected (401/403)
    MISSING       = "missing"        # no token file present
    INDETERMINATE = "indeterminate"  # transient/unknown (probe blip) — never act destructively


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
    # A fresh token means the session is healthy again — end any active re-auth alert episode
    # so the next time a wall appears the throttled email/push fires anew (best-effort).
    try:
        notify.clear_reauth_alert(env)
    except Exception:
        pass


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
            print("Tokens revoked and cache cleared.")
            return True
        print(f"  [Token] Revoke failed: HTTP {r.status_code}")
        return False
    except Exception as e:
        print(f"  [Token] Revoke error: {e}")
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


def _cooldown_remaining_from_state(state: dict) -> float:
    """Seconds left on the automated-reauth cooldown for an ALREADY-LOADED breaker state dict —
    the single home of the cooldown arithmetic. 0.0 means the gate is open."""
    return max(0.0, state["cooldown_until"] - time.time())


def _reauth_cooldown_remaining(env: str = "production") -> float:
    """Seconds left on the automated-reauth cooldown. 0.0 means the gate is open."""
    return _cooldown_remaining_from_state(_load_reauth_state(env))


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
# Persistent-profile trust state (gates the automated daily re-auth door)
# ---------------------------------------------------------------------------

def _profile_trust_state(env: str = "production") -> str:
    """Automated-reauth trust state for env: 'trusted' | 'sms_required' | 'unseeded'.

    'unseeded'     — no supervised bootstrap has proved the profile yet (default; missing file).
    'trusted'      — a bootstrap (or a prior zero-touch run) minted a token through the profile
                     browser; the automated door MAY open a browser.
    'sms_required' — a headless run hit the OTP wall; the automated door must NOT open a browser
                     until a human re-seeds via `aether etrade-login --bootstrap`.
    """
    try:
        with open(_TRUST_MARKER_PATH) as f:
            m = json.load(f)
    except (OSError, ValueError, TypeError):
        return "unseeded"
    if not isinstance(m, dict) or m.get("env") != env:
        return "unseeded"
    state = m.get("state")
    return state if state in ("trusted", "sms_required") else "unseeded"


def _set_profile_trust(env: str, state: str) -> None:
    """Persist the profile trust state ('trusted' or 'sms_required'). Best-effort."""
    try:
        os.makedirs(os.path.dirname(_TRUST_MARKER_PATH), exist_ok=True)
        with open(_TRUST_MARKER_PATH, "w") as f:
            json.dump({
                "env": env,
                "state": state,
                "updated_at": time.time(),
                "updated_date_et": _et_today(),
            }, f, indent=2)
        _log.info(f"E*TRADE {env} profile trust -> {state}")
    except OSError as e:
        _log.warning(f"E*TRADE: could not write trust marker: {e}")


def _scheduled_headless() -> bool:
    """Whether the automated daily re-auth (scheduled_reauth) runs headless. Default True; set
    AETHER_ETRADE_SCHEDULED_HEADLESS=0 (or config etrade_scheduled_headless=false) to run headful
    without a code change — the escape hatch if the prove stage finds headless trips Akamai and a
    real window survives the challenge."""
    v = os.environ.get("AETHER_ETRADE_SCHEDULED_HEADLESS")
    if v is None:
        v = str(getattr(CFG, "etrade_scheduled_headless", "") or "")
    return v.strip().lower() not in ("0", "false", "no", "off")


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
            _set_profile_trust(env, "trusted")  # a clean mint through the profile proves trust
            return tokens
    except SmsRequired:
        # OTP wall in a headless run: the browser and login WORKED — trust merely lapsed. That
        # is not a ban-risk failure, so retract the pre-armed breaker (don't let it escalate)
        # and propagate the signal so the caller latches sms_required and alerts a human.
        reset_reauth_circuit_breaker(env)
        raise SmsRequired(env)
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
        print(f"  [Auth] Could not auto-fill {step_name} — complete it manually in the browser.")
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
                print(f"  [Debug] Screenshot: {p}  |  URL: {page.url[:80]}")
            except Exception:
                pass

        try:
            print("Opening E*TRADE authorization page...")
            page.goto(auth_url, wait_until="domcontentloaded", timeout=30000)
            _snap("01_loaded")

            # How the login form gets filled depends on WHO drives it. Programmatic keystrokes
            # (page.type / page.press) carry a behavioral fingerprint E*TRADE's Akamai bot-check
            # flags — the "Log on" button then spins forever on the .../pxy/login URL and the flow
            # stalls. A HUMAN typing on a trusted device passes cleanly (and, device-trust intact,
            # is never even asked for SMS). So:
            #   * headless (automated daily run on an already-PROVEN profile) -> auto-fill + submit
            #   * headed  (supervised bootstrap / re-seed)                    -> the HUMAN types
            if headless:
                user_ok = _try_fill(page, _USER_SELECTORS, username, "username")
                pass_ok = _try_fill(page, _PASS_SELECTORS, password, "password")
                _snap("02_filled")
            else:
                user_ok = pass_ok = False   # skip the auto-submit + 30s auto-wait blocks below
                _snap("02_login_page")
                print("\n" + "=" * 68)
                print("  ACTION NEEDED - log in YOURSELF in the browser window:")
                print("    1. Type your User ID + password and click 'Log on'.")
                print("       (If the SCRIPT types, Akamai stalls the login; your own")
                print("        typing on this trusted device passes cleanly.)")
                print("    2. If asked, complete SMS and tick 'remember this device'.")
                print("    3. On the 'Authorize application' page, click Accept.")
                print("  Then return here - the verifier is captured automatically.")
                print("=" * 68 + "\n")
                try:
                    page.wait_for_function(_LEFT_LOGIN_PAGE_JS, timeout=180000)  # 3 min for the human
                    print(f"  [Auth] Login processed - page is now: {page.url[:80]}")
                except Exception:
                    print("  [Auth] Did not leave the login page within 3 min - inspecting state.")
                _snap("03b_post_login")
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

            # E*TRADE's login submit is an AJAX/SPA call: no full-document navigation fires, so
            # the domcontentloaded wait above returns while the "Log on" button is still spinning
            # on the SAME .../pxy/login URL. Proceeding immediately made the flow hunt for consent
            # controls on the login page itself (it toggled the login page's "Remember User ID"
            # box and found no Accept button), then poll fruitlessly for a verifier that never
            # appears. Wait for the page to actually LEAVE the login screen — the OAuth consent
            # page, the OTP wall, or any non-login URL — before running the OTP / Accept logic.
            # Bounded so a genuine Akamai spinner-hang still fails fast instead of blocking.
            if user_ok and pass_ok:
                try:
                    page.wait_for_function(_LEFT_LOGIN_PAGE_JS, timeout=30000)
                    print(f"  [Auth] Login processed — page is now: {page.url[:80]}")
                except Exception:
                    print("  [Auth] Still on the login page after 30s (slow render or Akamai "
                          "hold) — proceeding to inspect the current state.")
                _snap("03b_post_login")

            # Handle MFA / OTP step (sendotpcode page)
            if "sendotpcode" in page.url or "otp" in page.url.lower():
                _snap("04_otp_page")
                if headless:
                    # Automated/scheduled path: device-trust lapsed and E*TRADE wants a one-time
                    # SMS. No human is here — do NOT click "Send Code" (that fires a real SMS for
                    # nothing) and do NOT wait. Abort instantly with a distinct signal so the
                    # caller latches 'sms_required' and alerts a human. Close the context first so
                    # any partial Akamai cookie progress flushes to the persistent profile.
                    _log.warning("  [Auth] OTP wall hit in headless mode — aborting for manual SMS re-auth.")
                    try:
                        ctx.close()
                    except Exception:
                        pass
                    raise SmsRequired()
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

        except SmsRequired:
            # Device trust lapsed and the OTP wall appeared in a headless run. This is NOT a
            # browser-interaction failure (login worked) — propagate it intact so the caller
            # latches sms_required. The finally still runs; verifier is None so no state save.
            raise
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
            except SmsRequired:
                # Trust lapsed mid-renewal; don't swallow it as a generic error — let the
                # dedicated door (scheduled_reauth) latch sms_required and alert a human.
                raise
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

    print("Re-authenticating with browser...")

    oauth = pyetrade.ETradeOAuth(ck, cs)
    auth_url = oauth.get_request_token()
    print(f"Auth URL: {auth_url}")

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
    # A clean interactive mint through the profile browser (re-)proves device trust, so the
    # automated daily door is armed again after a monthly SMS bootstrap.
    _set_profile_trust(env, "trusted")
    print("Tokens saved to cache.")
    return tokens


def _breaker_summary(env: str = "production", *, state: dict | None = None) -> dict:
    """JSON-friendly snapshot of the automated-reauth breaker for the re-auth result dict.
    Pass an already-loaded `state` to reuse a single read of the breaker file (else it reads
    its own)."""
    st = state if state is not None else _load_reauth_state(env)
    return {
        "consecutive_failures": st["consecutive_failures"],
        "cooldown_remaining_min": round(_cooldown_remaining_from_state(st) / 60, 1),
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
    JSON-serializable dict so the HTTP endpoint and CLI render it uniformly. This is the
    HUMAN door. The scheduler now has its OWN dedicated automated door, scheduled_reauth(),
    which is gated by the persistent-profile trust marker + circuit breaker + a once/day guard
    and never opens a browser while trust is down — so the anti-ban contract holds: an
    unattended browser opens at most once/day, only through that door, only while trusted.
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


def _dead_token_gate(trust: str, cooling: bool):
    """Classify why a dead/absent token can't be auto-refreshed right now — the single
    definition of the trust->breaker gate, shared by the read-only classifier auth_status()
    and the acting door scheduled_reauth().

    Returns (reason, needs_manual, can_auto):
      * trust lapsed (sms_required/unseeded) -> a human must re-seed the profile.
      * breaker cooling                       -> neither human nor automation acts yet.
      * trusted + breaker clear (reason=None) -> the automated door can refresh unattended.
    trust's non-'trusted' values equal the AuthReason.SMS_REQUIRED/UNSEEDED strings, so they
    are returned verbatim as the reason.
    """
    if trust in ("sms_required", "unseeded"):
        return trust, True, False
    if cooling:
        return AuthReason.BREAKER, False, False
    return None, False, True


def _auth_summary(env: str, r: dict) -> str:
    """Human one-liner for an auth_status verdict — points at the exact next action."""
    state = r["state"]
    boot = "`aether etrade-login --bootstrap`"
    if state == AuthReason.LIVE:
        probe = r["probe"]
        if probe == "authorized":
            return f"E*TRADE {env}: authorized (broker-confirmed)."
        if probe == "indeterminate":
            return (f"E*TRADE {env}: token present; broker probe inconclusive (transient) — "
                    f"treating as live.")
        return f"E*TRADE {env}: token valid (issued today)."
    if state == AuthReason.MISSING:
        return (f"E*TRADE {env}: no token — supervised bootstrap required ({boot})."
                if r["needs_manual_auth"]
                else f"E*TRADE {env}: no token — the daily automated re-auth will mint one.")
    if state == AuthReason.EXPIRED:
        return f"E*TRADE {env}: token expired — the daily automated re-auth will refresh it."
    if state == AuthReason.SMS_REQUIRED:
        return f"E*TRADE {env}: device trust lapsed — a one-time SMS bootstrap is required ({boot})."
    if state == AuthReason.UNSEEDED:
        return f"E*TRADE {env}: profile not seeded — a supervised bootstrap is required ({boot})."
    if state == AuthReason.BREAKER:
        return (f"E*TRADE {env}: automated re-auth cooling down "
                f"({r['breaker']['cooldown_remaining_min']} min left).")
    return f"E*TRADE {env}: {state}."


def auth_status(env: str = "production", *, probe: bool = False) -> dict:
    """Read-only E*TRADE auth-state classifier, shared by every surface (the /api/etrade/status
    endpoint, the /api/health embed, and the `server.py etrade-status` CLI all call this function).
    The read-only mirror of the automated door scheduled_reauth(): it reuses the AuthReason
    vocabulary and the _dead_token_gate trust/breaker gate, but NEVER opens a browser.

    Local-check -> refresh -> probe ladder (mirrors scheduled_reauth's renew-first order):
      * probe=False (default) -> pure-local: no network, no mutation. Cheap enough to embed in
        a frequently-polled health blob; the trust/breaker state alone already surfaces whether
        a human bootstrap is needed.
      * probe=True -> for a SAME-DAY token, first REFRESH via the ban-free HTTP renew (fixes the
        2 h idle timeout cheaply), then PROBE the refreshed token against the broker
        (_probe_token_auth) for ground truth. A previous-day token is an overnight/midnight-ET
        hard expiry that renew cannot bridge, so it is ruled dead locally with NO wasted probe.

    Returns a JSON-serializable dict. Every non-live state carries needs_manual_auth /
    can_auto_reauth so any caller decides what to do without re-deriving the state machine.
    """
    trust   = _profile_trust_state(env)
    _bstate = _load_reauth_state(env)          # one read of the breaker file, reused below
    breaker = _breaker_summary(env, state=_bstate)
    # Cool off the RAW remaining seconds, not the display-rounded cooldown_remaining_min (rounded
    # to 1 decimal, so it reads clear for the last ~3 s of a cooldown that scheduled_reauth still
    # honors) — derived from the same loaded state, so no extra read and no rounding drift.
    cooling = _cooldown_remaining_from_state(_bstate) > 0
    tokens  = _load_tokens_any_date(env)
    issued  = tokens.get("issued_date_et") if tokens else None
    is_today = bool(tokens) and issued == _et_today()

    result = {
        "env": env,
        "state": None,
        "reason": None,
        "needs_manual_auth": False,
        "can_auto_reauth": False,
        "token": {"present": bool(tokens), "issued_date_et": issued, "is_today": is_today},
        "trust": trust,
        "breaker": breaker,
        "probe": "skipped",
        "checked_at_et": _et_now(),
        "summary": "",
    }

    def _finalize(state, reason, *, needs_manual, can_auto):
        result["state"] = state
        result["reason"] = reason
        result["needs_manual_auth"] = needs_manual
        result["can_auto_reauth"] = can_auto
        result["summary"] = _auth_summary(env, result)
        return result

    def _dead(state):
        # Token is absent or dead. Why it can't be auto-refreshed is the SAME trust->breaker gate
        # scheduled_reauth uses, via the shared _dead_token_gate. reason=None means trusted+clear:
        # report the raw dead-state (missing/expired) and let the automated door fix it.
        gate_reason, needs_manual, can_auto = _dead_token_gate(trust, cooling)
        st = gate_reason or state
        return _finalize(st, st, needs_manual=needs_manual, can_auto=can_auto)

    # 1. Local, no network.
    if not tokens:
        return _dead(AuthReason.MISSING)
    if not is_today:
        # Previous-day token = overnight/midnight-ET hard expiry; renew cannot bridge it and a
        # probe would only confirm rejected -> rule dead locally, skip both refresh and probe.
        return _dead(AuthReason.EXPIRED)
    if not probe:
        # Same-day token, cheap path: valid per local record (no network touched).
        return _finalize(AuthReason.LIVE, AuthReason.LIVE, needs_manual=False, can_auto=False)

    # 2. Refresh (same-day, ban-free HTTP; the 55-min guard reuses a fresh token with no call).
    orig_saved_at = tokens.get("saved_at")
    renewed = renew_tokens(tokens, env)
    live_tokens = renewed or tokens
    # A real renew stamps a new saved_at (renew_tokens); a <55-min guard-reuse returns the same
    # token with saved_at unchanged. Comparing saved_at — not re-checking the 55-min threshold
    # (a second source of truth) and not just bool(renewed) (true for a reuse too) — is what
    # separates a genuine refresh from a reuse, so RENEWED means the token really changed.
    did_renew = bool(renewed) and live_tokens.get("saved_at") != orig_saved_at

    # 3. Probe the (possibly-renewed) token for broker ground truth.
    verdict = _probe_token_auth(live_tokens, env)
    if verdict is True:
        result["probe"] = "authorized"
        reason = AuthReason.RENEWED if did_renew else AuthReason.LIVE
        return _finalize(AuthReason.LIVE, reason, needs_manual=False, can_auto=False)
    if verdict is False:
        result["probe"] = "rejected"
        return _dead(AuthReason.EXPIRED)
    # None -> transient/indeterminate: never downgrade a same-day token on a blip.
    result["probe"] = "indeterminate"
    return _finalize(AuthReason.LIVE, AuthReason.INDETERMINATE, needs_manual=False, can_auto=False)


def scheduled_reauth(env: str = "production") -> dict:
    """The ONE automated (unattended) E*TRADE re-auth door — safe by construction.

    Called only by the daily Task-Scheduler entry (server.py `etrade-reauth --scheduled`) and
    the watchdog catch-up. Opens a browser at most ONCE per invocation, and only when ALL of
    these hold, in order:
      1. keep_alive() couldn't renew a live same-day token (pure HTTP, no browser). If it
         could, we return renewed with NO browser.
      2. The persistent-profile trust marker reads 'trusted'. If it is 'sms_required' or
         'unseeded', we open NO browser and return that reason so the caller alerts a human.
      3. The circuit breaker isn't cooling.
    Only then does it call _login_headless() DIRECTLY (the single breaker/trust choke point) —
    not get_tokens()'s priority-3 ladder, which many non-scheduled callers (pricing) hit and
    must never pop a browser through. A headless OTP wall raises SmsRequired, which latches the
    marker to 'sms_required' (no more automated browsers until a human bootstraps) and reports.

    The "at most one browser per day" property is not a standalone gate but an emergent one:
    step 1 renews any live same-day token first (so a fresh mint is reused, not re-opened); a
    failed attempt arms the breaker (step 3 blocks the rest of the day); and an OTP wall latches
    'sms_required' (step 2 blocks it) — so after any outcome of the first open, same-day reruns
    do not open a second browser.

    Returns a JSON-serializable dict: {ok, env, reason, browser_opened, ...}. `reason` is one
    of: renewed | reauthed | sms_required | unseeded | breaker | failed.
    """
    result = {"ok": False, "env": env, "reason": "", "browser_opened": False,
              "issued_date_et": None, "breaker_state": None}

    # 1. Renew-first: a still-live same-day token needs no browser at all (ban-free HTTP).
    alive = keep_alive(env)
    if alive:
        result.update(ok=True, reason=AuthReason.RENEWED, issued_date_et=alive.get("issued_date_et"))
        result["breaker_state"] = _breaker_summary(env)
        return result

    # 2-3. Token is dead. The automated door opens a browser ONLY while the profile is trusted
    # AND the breaker is clear — the SAME gate the read-only auth_status reports, via the shared
    # _dead_token_gate (trust lapse 'sms_required'/'unseeded' -> no browser, awaiting a human;
    # cooling -> 'breaker'). reason=None means both gates are open.
    trust = _profile_trust_state(env)
    gate_reason, _needs_manual, _can_auto = _dead_token_gate(
        trust, _reauth_cooldown_remaining(env) > 0)
    if gate_reason is not None:
        result["reason"] = gate_reason
        result["breaker_state"] = _breaker_summary(env)
        return result

    # All gates passed → the one allowed automated browser open, through the breaker choke point.
    ck, cs, username, password = _load_config(env)
    try:
        tokens = _login_headless(ck, cs, username, password, env, headless=_scheduled_headless())
    except SmsRequired:
        # Trust lapsed: the browser+login worked but E*TRADE demanded an OTP. Latch the marker
        # so NO further automated browser opens until a human re-seeds; breaker already cleared.
        _set_profile_trust(env, "sms_required")
        result.update(reason=AuthReason.SMS_REQUIRED, browser_opened=True)
        result["breaker_state"] = _breaker_summary(env)
        return result

    result["browser_opened"] = True
    if tokens:
        result.update(ok=True, reason=AuthReason.REAUTHED, issued_date_et=tokens.get("issued_date_et"))
    else:
        # _login_headless already escalated the breaker (or was suppressed by it).
        result["reason"] = AuthReason.FAILED
    result["breaker_state"] = _breaker_summary(env)
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
# Object facade (encapsulation / extensibility)
# ---------------------------------------------------------------------------
# Imported at the BOTTOM, after every free function/constant above is defined, so
# these submodules can `from aether import etrade` and resolve a fully-initialised
# package. They touch the package only at call time, so there is no circular-import
# hazard. The proven free-function API above is UNCHANGED and remains the back-compat
# seam every test patches; ETradeClient is the new front door that delegates to it.
from aether.etrade.store import (          # noqa: E402
    make_etrade_store, EtradeStore,
    TokenStore, BrowserStateStore, ReauthStateStore,
)
from aether.etrade.client import (                                 # noqa: E402
    ETradeClient, ETradeError, RoleNotPermitted,
)


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

    print("\n--- Quote: AAPL ---")
    market = get_market(tokens)
    print(market.get_quote(["AAPL"], resp_format="json"))

    print("\n--- Accounts ---")
    accts = get_accounts(tokens)
    print(accts.list_accounts(resp_format="json"))
