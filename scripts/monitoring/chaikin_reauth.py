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
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import powergauge as pg
from aether_logger import get_logger as _get_logger

_log = _get_logger("chaikin_reauth")


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


def main() -> int:
    ap = argparse.ArgumentParser(description="Chaikin proactive weekly re-authentication.")
    ap.add_argument("--force", action="store_true",
                    help="Mint a fresh token via the headed browser even if the current one is valid.")
    ap.add_argument("--min-days", type=float, default=3.0,
                    help="Refresh when the sessionToken has fewer than this many days of runway (default 3).")
    ap.add_argument("--check", action="store_true",
                    help="Only report runway; never launch a browser.")
    args = ap.parse_args()

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
        return 1

    if not (new_session and new_session.get("jsessionid")):
        _log.error("Re-auth returned no session id.")
        return 1
    if pg._probe_session(new_session) != "valid":
        _log.error("Re-auth minted a session that did NOT probe valid.")
        return 1

    new_exp = _decode_exp(new_session.get("jwttoken") or "")
    _log.info("Re-auth OK — new sessionToken exp=%s UTC; probe=valid.",
              new_exp.isoformat() if new_exp else "unknown")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
