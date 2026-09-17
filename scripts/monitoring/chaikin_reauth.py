"""
Chaikin proactive weekly re-authentication.

The Chaikin `sessionToken` (JWT in `Data/session.json`) expires only ~7 days out, so
if the daily pipeline ever pauses for a week the token lapses and every PGR fetch
starts failing. This script refreshes it PROACTIVELY, ahead of expiry, with no human:

  1. Decode the current sessionToken's `exp` and report days of runway.
  2. If it is still comfortably valid (>= --min-days, default 3) and not --force,
     do nothing — the daily pipeline's in-window bypass keeps the sessionKey fresh.
  3. Otherwise run the HEADED persistent-context browser login
     (powergauge._login_via_browser). The profile's long-lived cf_clearance cookie
     lets Cloudflare Turnstile auto-pass with NO interaction, minting a fresh 7-day
     sessionToken and re-warming cf_clearance. Headless is deliberately NOT used — it
     trips Turnstile even with cf_clearance (see plans/chaikin_api.md).

Run it as a weekly Windows Scheduled Task in the DESKTOP (interactive) session — headed
Chrome needs a visible session, but the flow itself requires no clicks.

    python scripts/monitoring/chaikin_reauth.py            # refresh only if near expiry
    python scripts/monitoring/chaikin_reauth.py --force    # always mint a fresh token
    python scripts/monitoring/chaikin_reauth.py --check    # report runway, never launch a browser

Per the Mandatory Backup Policy, session.json is copied to Data/Backup/ before any write.
"""
import argparse
import base64
import datetime
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import powergauge as pg
from aether import chaikin_cdp as cdp  # guards its own Playwright import (None if absent)
from aether.notify import send_email
from aether_logger import get_logger as _get_logger

_log = _get_logger("chaikin_reauth")

# CDP-attach 1-click reauth defaults.
_CDP_PORT = 9222
_CDP_ENDPOINT = f"http://localhost:{_CDP_PORT}"
_MEMBERS_LOGIN_URL = "https://members.chaikinanalytics.com/login"
# Throttle state for reminder emails (kind -> last-sent epoch seconds).
_NOTIFY_STATE = os.path.join(os.path.dirname(os.path.abspath(pg.SESSION_FILE)),
                             "chaikin_reauth_notify.json")


def _decode_exp(jwt_token: str) -> datetime.datetime | None:
    """Return the sessionToken's expiry as a tz-aware UTC datetime, or None if undecodable."""
    try:
        payload_b64 = jwt_token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)  # restore base64url padding
        exp = json.loads(base64.urlsafe_b64decode(payload_b64)).get("exp")
        if exp is None:
            return None
        return datetime.datetime.fromtimestamp(int(exp), tz=datetime.timezone.utc)
    except Exception:
        return None


def _backup_session() -> None:
    """Clone Data/session.json to a timestamped Data/Backup/ file (Mandatory Backup Policy)."""
    if not os.path.exists(pg.SESSION_FILE):
        return
    backup_dir = os.path.join(os.path.dirname(pg.SESSION_FILE), "Backup")
    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(backup_dir, f"session_{stamp}.json")
    shutil.copy2(pg.SESSION_FILE, dst)
    _log.info("Backed up session.json -> %s", dst)


# ── CDP-attach 1-click reauth (launch real Chrome, human logs in, capture token) ──────────

def _find_chrome() -> str | None:
    """Locate chrome.exe in the standard Windows install locations, or None."""
    candidates = [
        os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"),
                     "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
                     "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""),
                     "Google", "Chrome", "Application", "chrome.exe"),
    ]
    return next((c for c in candidates if c and os.path.exists(c)), None)


def _cdp_alive(endpoint: str = _CDP_ENDPOINT, timeout: float = 2.0) -> bool:
    """True if a debug Chrome answers at <endpoint>/json/version (proxy explicitly bypassed)."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(endpoint.rstrip("/") + "/json/version", timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def _launch_chrome(port: int, profile_dir: str, url: str) -> subprocess.Popen:
    """Launch a REAL chrome.exe with the debug port on the persistent Chaikin profile."""
    chrome = _find_chrome()
    if not chrome:
        raise FileNotFoundError("chrome.exe not found in standard install locations.")
    os.makedirs(profile_dir, exist_ok=True)
    return subprocess.Popen([  # noqa: S603 - fixed chrome.exe path + our own args
        chrome,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        url,
    ])


def _session_from_store(store: dict) -> dict:
    """Map a warm tab's localStorage into the Data/session.json contract.

    jwttoken=sessionToken, jsessionid=jsessionId (members camelCase is authoritative;
    == sessionKey), uuid=email. Returns {} if no sessionToken is present.
    """
    token = (store or {}).get("sessionToken")
    if not token:
        return {}
    return {
        "jwttoken": token,
        "jsessionid": store.get("jsessionId") or store.get("sessionKey")
        or store.get("sessionId") or "",
        "uuid": store.get("email") or "",
    }


def _poll_for_login(endpoint: str, deadline: float) -> dict:
    """Poll the attached Chrome until a non-expired sessionToken appears, or the deadline."""
    while time.time() < deadline:
        try:
            store, _origin = cdp.connect_and_capture(endpoint, log=lambda m: _log.info("%s", m))
        except (cdp.CdpUnreachable, cdp.NoChaikinTab):
            time.sleep(2.0)
            continue
        session = _session_from_store(store)
        exp = _decode_exp(session.get("jwttoken") or "")
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        if session and session.get("jsessionid") and exp and exp > now:
            return session
        time.sleep(2.0)
    return {}


def _email_throttled(kind: str, min_hours: float) -> bool:
    """True if an email of `kind` was sent within `min_hours`. On False, stamps 'now' first so
    callers can simply guard `if not _email_throttled(...): send_email(...)`."""
    now = datetime.datetime.now(tz=datetime.timezone.utc).timestamp()
    state = {}
    try:
        if os.path.exists(_NOTIFY_STATE):
            with open(_NOTIFY_STATE, encoding="utf-8") as fh:
                state = json.load(fh) or {}
    except Exception:
        state = {}
    last = state.get(kind)
    if isinstance(last, (int, float)) and (now - last) < min_hours * 3600.0:
        return True
    state[kind] = now
    try:
        os.makedirs(os.path.dirname(_NOTIFY_STATE), exist_ok=True)
        with open(_NOTIFY_STATE, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
    except Exception:
        _log.warning("Could not persist notify-throttle state to %s", _NOTIFY_STATE)
    return False


def _notify_manual_reauth(reason: str) -> None:
    """Throttled (6h) email telling the user to click the System login button."""
    if _email_throttled("reauth_failed", 6.0):
        _log.info("Manual-reauth email suppressed (throttled within 6h).")
        return
    try:
        send_email(
            subject="Chaikin auto-reauth failed - click the System login button",
            body=("The weekly Chaikin token auto-refresh could not renew the session "
                  f"(reason: {reason}).\n\n"
                  "Open the AETHER dashboard System page and click "
                  "'Chaikin - Login & Refresh Token (opens Chrome)', then log in once "
                  "(one Turnstile). A fresh ~7-day token is captured automatically."),
        )
        _log.info("Manual-reauth reminder emailed.")
    except Exception as mail_err:
        _log.warning("Failed to send manual-reauth email: %s", mail_err)


def _run_cdp_reauth(args) -> int:
    """1-click CDP-attach reauth: launch (or attach to) a real Chrome, wait for the human to
    log in by hand, capture the token from localStorage, back up, save, and probe."""
    cdp.ensure_localhost_noproxy()
    endpoint = args.cdp_endpoint

    if _cdp_alive(endpoint):
        _log.console("Debug Chrome already listening at %s - attaching.", endpoint)
    elif args.no_launch:
        _log.error("No debug Chrome at %s and --no-launch set; nothing to attach to.", endpoint)
        return 2
    else:
        if not _find_chrome():
            _log.error("chrome.exe not found. Launch Chrome manually with "
                       "--remote-debugging-port=%d --user-data-dir=%s and re-run with --no-launch.",
                       _CDP_PORT, pg._CHAIKIN_PROFILE_DIR)
            _notify_manual_reauth("chrome.exe not found for the CDP flow")
            return 2
        _log.console("Launching Chrome at the Chaikin login page. Log in by hand (one Turnstile); "
                     "capture is automatic.")
        try:
            _launch_chrome(_CDP_PORT, pg._CHAIKIN_PROFILE_DIR, _MEMBERS_LOGIN_URL)
        except Exception as e:
            _log.error("Failed to launch Chrome: %s", e)
            _notify_manual_reauth(f"could not launch Chrome ({e})")
            return 2
        port_deadline = time.time() + 30
        while time.time() < port_deadline and not _cdp_alive(endpoint):
            time.sleep(1.0)
        if not _cdp_alive(endpoint):
            _log.error("Chrome launched but debug port %s never came up. If a normal Chrome was "
                       "already open on this profile, close ALL Chrome windows and retry.", endpoint)
            _notify_manual_reauth("debug port never opened (Chrome may already be running on the "
                                  "profile without --remote-debugging-port)")
            return 2

    deadline = time.time() + args.login_timeout
    _log.console("Waiting up to %ds for you to finish logging in...", args.login_timeout)
    session = _poll_for_login(endpoint, deadline)
    if not session:
        _log.error("No valid Chaikin session captured within %ds (login not completed / Turnstile "
                   "not solved).", args.login_timeout)
        _notify_manual_reauth("login not completed within the CDP capture window")
        return 1

    _backup_session()
    pg._save_session_to_file(session)
    probe = pg._probe_session(session)
    new_exp = _decode_exp(session.get("jwttoken") or "")
    if probe == "valid":
        _log.console("Captured fresh Chaikin token (exp=%s UTC); probe=valid.",
                     new_exp.isoformat() if new_exp else "unknown")
        return 0
    _log.error("Captured a token that did NOT probe valid (probe=%s).", probe)
    _notify_manual_reauth(f"captured token did not probe valid (probe={probe})")
    return 4


def main() -> int:
    ap = argparse.ArgumentParser(description="Chaikin proactive weekly re-authentication.")
    ap.add_argument("--force", action="store_true",
                    help="Mint a fresh token via the headed browser even if the current one is valid.")
    ap.add_argument("--min-days", type=float, default=3.0,
                    help="Refresh when the sessionToken has fewer than this many days of runway (default 3).")
    ap.add_argument("--check", action="store_true",
                    help="Only report runway; never launch a browser.")
    ap.add_argument("--cdp", action="store_true",
                    help="1-click reauth: open a real Chrome, log in by hand once, and capture "
                         "the fresh token over CDP (the ONLY renewal proven to pass Turnstile).")
    ap.add_argument("--cdp-endpoint", default=_CDP_ENDPOINT,
                    help=f"Chrome DevTools endpoint for --cdp (default {_CDP_ENDPOINT}).")
    ap.add_argument("--login-timeout", type=int, default=600,
                    help="Seconds to wait for the human login in --cdp mode (default 600).")
    ap.add_argument("--no-launch", action="store_true",
                    help="In --cdp mode, attach to an already-running debug Chrome; do not launch one.")
    ap.add_argument("--notify-days", type=float, default=None,
                    help="Runway-watch mode: if the sessionToken has fewer than this many days of "
                         "runway, email a reminder to click the System login button. Never launches "
                         "a browser; throttled to at most one email/day.")
    args = ap.parse_args()

    if args.cdp:
        return _run_cdp_reauth(args)

    session = pg._load_session_from_file()
    jwt = (session or {}).get("jwttoken") or ""
    exp = _decode_exp(jwt)
    now = datetime.datetime.now(tz=datetime.timezone.utc)

    if exp is not None:
        runway_days = (exp - now).total_seconds() / 86400.0
        _log.info("sessionToken exp=%s UTC (%.2f days runway); probe=%s",
                  exp.isoformat(), runway_days, pg._probe_session(session) if session else "no-session")
    else:
        runway_days = -1.0  # unknown/undecodable -> treat as needing refresh
        _log.warning("sessionToken missing or undecodable — treating as expired.")

    if args.notify_days is not None:
        # Runway-watch: a browser-free daily nudge before expiry. Never launches Chrome.
        if runway_days < args.notify_days:
            if _email_throttled("runway_low", 20.0):
                _log.info("Runway %.2fd < %.1fd, but reminder already sent within 20h — skipping.",
                          runway_days, args.notify_days)
            else:
                try:
                    send_email(
                        subject="Chaikin token expiring soon - refresh it from the System page",
                        body=(f"The Chaikin sessionToken has {runway_days:.1f} days of runway left "
                              f"(threshold {args.notify_days:.1f}).\n\n"
                              "Open the AETHER dashboard System page and click "
                              "'Chaikin - Login & Refresh Token (opens Chrome)', then log in once "
                              "(one Turnstile). A fresh ~7-day token is captured automatically."),
                    )
                    _log.info("Runway-watch reminder emailed (%.2fd < %.1fd).",
                              runway_days, args.notify_days)
                except Exception as mail_err:
                    _log.warning("Failed to send runway-watch email: %s", mail_err)
        else:
            _log.info("Runway %.2fd >= %.1fd — no reminder needed.", runway_days, args.notify_days)
        return 0

    if args.check:
        return 0

    needs_refresh = args.force or runway_days < args.min_days
    if not needs_refresh:
        _log.info("Token has %.2f days runway (>= %.1f) — no re-auth needed.", runway_days, args.min_days)
        return 0

    _log.info("Refreshing Chaikin session via headed persistent-context browser login...")
    _backup_session()
    try:
        # Headed by default (CHAIKIN_HEADLESS_LOGIN is intentionally left unset). This
        # mints a fresh 7-day sessionToken AND re-warms the profile's cf_clearance.
        new_session = pg._login_via_browser(headless=False)
    except Exception as e:
        _log.error("Headed re-auth failed: %s", e)
        _notify_manual_reauth(f"headed persistent-context login failed ({e})")
        return 1

    if not (new_session and new_session.get("jsessionid")):
        _log.error("Re-auth returned no session id.")
        _notify_manual_reauth("headed re-auth returned no session id")
        return 1
    if pg._probe_session(new_session) != "valid":
        _log.error("Re-auth minted a session that did NOT probe valid.")
        _notify_manual_reauth("headed re-auth minted a token that did not probe valid")
        return 1

    new_exp = _decode_exp(new_session.get("jwttoken") or "")
    _log.info("Re-auth OK — new sessionToken exp=%s UTC; probe=valid.",
              new_exp.isoformat() if new_exp else "unknown")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
