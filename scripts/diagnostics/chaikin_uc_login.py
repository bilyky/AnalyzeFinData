"""
=====================================================================================
EXPERIMENTAL / UNRELIABLE — undetected-chromedriver (UC) Chaikin login RESEARCH PROBE
=====================================================================================

This is a RESEARCH prototype, NOT a production path. It exists only to measure whether
`undetected_chromedriver` can pass Cloudflare Turnstile on the Chaikin members login
where plain Playwright-driven Chrome CANNOT (Playwright trips Turnstile error 600010 —
an automation fingerprint, not an IP problem). It is EXPECTED to fail more often than it
succeeds, and its result must NEVER be trusted as a token source.

    THE PROVEN, SUPPORTED RENEWAL REMAINS THE HUMAN CDP-ATTACH FLOW:
        python scripts/monitoring/chaikin_reauth.py --cdp
    (a real human passes Turnstile once; the script reads the warm tab's localStorage).

Deliberate isolation (do not remove):
- This script is NOT registered in `data_api.MANUAL_TASKS`, NOT in any scheduler, and is
  imported by NO production module. It is a hand-run diagnostic only.
- It NEVER writes `Data/session.json`. On a successful probe it writes AT MOST a scratch
  `Data/chaikin_uc_probe.json` (fingerprints + runway, never raw token values) so a human
  can decide whether the approach is worth pursuing. The production session file is left
  untouched — promotion, if it ever happens, is a separate deliberate step.
- Token VALUES are never printed, logged, or persisted — only `len=NN sha=<8hex>`
  fingerprints, enough to tell whether a token is present / changed without leaking it.
- It reuses powergauge's persistent Chrome profile (for the durable cf_clearance cookie)
  and Chaikin credentials, but performs its OWN login attempt via Selenium/UC.

Exit codes:
    0  PASS  — UC login produced a non-empty sessionToken (runway reported).
    1  FAIL  — UC launched but login/Turnstile did not yield a sessionToken.
    3  SKIP  — undetected_chromedriver (or Selenium) is not installed.

Usage (run by hand, in a real desktop session — UC needs a visible browser):
    python scripts/diagnostics/chaikin_uc_login.py
    python scripts/diagnostics/chaikin_uc_login.py --headless   # research only; usually FAILS
    python scripts/diagnostics/chaikin_uc_login.py --timeout 90
"""
import argparse
import base64
import datetime
import hashlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import powergauge as pg
from aether_logger import get_logger as _get_logger

# Optional heavy deps — guarded at module top (AETHER forbids inline imports). undetected-
# chromedriver pulls in Selenium, so if `uc` imports, the Selenium names import too.
try:
    import undetected_chromedriver as uc
    from selenium.common.exceptions import TimeoutException, WebDriverException
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait
except ImportError:
    uc = None

_log = _get_logger("chaikin_uc")

_MEMBERS_LOGIN_URL = "https://members.chaikinanalytics.com/login"
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PROBE_OUT = os.path.join(_ROOT, "Data", "chaikin_uc_probe.json")


def _fp(value) -> str:
    """Non-reversible fingerprint of a secret: length + short sha256 prefix. Never the value."""
    if value is None:
        return "None"
    s = str(value)
    if s == "":
        return "len=0 (empty)"
    h = hashlib.sha256(s.encode("utf-8", "replace")).hexdigest()[:8]
    return f"len={len(s)} sha={h}"


def _decode_exp(jwt_token: str):
    """Return the sessionToken's `exp` as a tz-aware UTC datetime, or None if undecodable."""
    try:
        payload_b64 = jwt_token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)  # restore base64url padding
        exp = json.loads(base64.urlsafe_b64decode(payload_b64)).get("exp")
        if exp is None:
            return None
        return datetime.datetime.fromtimestamp(int(exp), tz=datetime.timezone.utc)
    except Exception:
        return None


def _build_driver(headless: bool):
    """Launch an undetected-chromedriver Chrome on powergauge's persistent profile."""
    os.makedirs(pg._CHAIKIN_PROFILE_DIR, exist_ok=True)
    options = uc.ChromeOptions()
    options.add_argument(f"--user-data-dir={pg._CHAIKIN_PROFILE_DIR}")
    if headless:
        options.add_argument("--headless=new")
    # UC picks a matching driver for the installed Chrome; keep the window real so a human
    # can solve Turnstile in the (expected) cold-profile case.
    return uc.Chrome(options=options)


def _attempt_login(driver, timeout: int) -> str:
    """Drive the members login and return the sessionToken from localStorage ('' if none)."""
    driver.get(_MEMBERS_LOGIN_URL)
    wait = WebDriverWait(driver, timeout)

    email, password = pg._load_credentials()
    wait.until(EC.presence_of_element_located((By.NAME, "email"))).send_keys(email)
    driver.find_element(By.NAME, "password").send_keys(password)

    # Turnstile enables the submit button once it clears (auto with a warm cf_clearance;
    # a human clicks the widget in the cold case). This is the step that usually FAILS.
    _log.info("Waiting up to %ds for Turnstile to enable submit (click the widget if shown)...",
              timeout)
    wait.until(EC.element_to_be_clickable(
        (By.CSS_SELECTOR, 'button[type="submit"]:not([disabled])'))).click()

    # Wait for the SPA to leave /login, then let it populate localStorage.
    try:
        wait.until(lambda d: "/login" not in d.current_url)
    except TimeoutException:
        _log.warning("URL still on /login after %ds — Turnstile/login likely did not pass.",
                     timeout)
    time.sleep(6)
    token = driver.execute_script("return window.localStorage.getItem('sessionToken');")
    return token or ""


def _write_probe(token: str, exp) -> None:
    """Write a fingerprint-only scratch record (NEVER the token value, NEVER session.json)."""
    record = {
        "captured_at": datetime.datetime.now(tz=datetime.timezone.utc).isoformat(),
        "sessionToken_fp": _fp(token),
        "exp": exp.isoformat() if exp else None,
        "note": "UC research probe — fingerprints only; not a production session file.",
    }
    os.makedirs(os.path.dirname(_PROBE_OUT), exist_ok=True)
    with open(_PROBE_OUT, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2)
    _log.info("Wrote scratch probe record to %s", _PROBE_OUT)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headless", action="store_true",
                        help="Research only — headless UC usually FAILS Turnstile.")
    parser.add_argument("--timeout", type=int, default=90,
                        help="Seconds to wait for Turnstile / login (default 90).")
    args = parser.parse_args()

    _log.warning("EXPERIMENTAL UC probe — NOT a supported renewal path. The proven flow is "
                 "`chaikin_reauth.py --cdp` (human CDP-attach).")

    if uc is None:
        _log.error("undetected_chromedriver / selenium not installed — SKIP (exit 3).")
        _log.error("  Install for research only:  pip install undetected-chromedriver selenium")
        return 3

    driver = None
    try:
        driver = _build_driver(args.headless)
    except WebDriverException as e:
        _log.error("UC failed to launch Chrome: %s", e)
        return 1

    try:
        token = _attempt_login(driver, args.timeout)
    except (WebDriverException, EnvironmentError) as e:
        _log.error("UC login attempt failed: %s", e)
        return 1
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    if not token:
        _log.error("FAIL — no sessionToken in localStorage after login (Turnstile/timeout). "
                   "This is the EXPECTED outcome for UC; use `chaikin_reauth.py --cdp`.")
        return 1

    exp = _decode_exp(token)
    if exp is not None:
        runway = (exp - datetime.datetime.now(tz=datetime.timezone.utc)).total_seconds() / 86400.0
        _log.info("PASS — UC captured a sessionToken: %s · exp=%s (%.2f days runway).",
                  _fp(token), exp.isoformat(), runway)
    else:
        _log.info("PASS — UC captured a sessionToken: %s (exp undecodable).", _fp(token))
    _write_probe(token, exp)
    _log.info("Note: probe is a RESEARCH result only — Data/session.json was NOT modified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
