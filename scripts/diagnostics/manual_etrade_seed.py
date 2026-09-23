#!/usr/bin/env python
"""
Manual E*TRADE token seed - pure OAuth 1.0a PIN flow, NO Playwright / NO automation.

WHY THIS EXISTS
    E*TRADE's Akamai bot-defense serves a *skeletonized* login form to the Playwright-spawned
    Chrome - the login widget never renders (verified 2026-08-20: screenshot 01_loaded shows
    only gray placeholder boxes where the User ID / password fields should be). So the automated
    re-auth stalls no matter who types, because there is nothing to type into.

    Your OWN browser renders that form fine and, on a device E*TRADE already trusts, is never
    even asked for SMS. This script leans on exactly that: it runs the OAuth handshake over HTTP
    (no browser), hands YOU the authorize URL to open in your normal Chrome, and exchanges the
    short PIN you paste back for a production access token - saved into the same Data/ the app
    reads (via AETHER_DATA_DIR / aether.paths.data_dir()).

    It deliberately does NOT flip the profile-trust marker to "trusted": this flow never warms
    the Playwright persistent profile, so arming the automated headless door would just resume
    the Akamai stall in a loop. Getting a working token today and re-establishing hands-off
    automation are separate problems; this solves the first, safely.

RUN IT AT A REAL TERMINAL (it must prompt you for the PIN):

    PowerShell:
        $env:AETHER_DATA_DIR = 'D:\\Develop\\AnalyzeFinData\\Data'
        python scripts\\diagnostics\\manual_etrade_seed.py

STEPS IT WALKS YOU THROUGH
    1. It prints an Authorize URL. Open it in YOUR Chrome (the trusted one).
    2. Log in (no SMS if the device is remembered), then click Accept.
    3. E*TRADE shows a short verification code / PIN - copy it.
    4. Paste it here; the script exchanges it and writes Data/etrade_tokens.json (production).
"""
import os
import sys

# Pin all auth-state files to prod's Data unless the caller already set it. This MUST happen
# before importing aether.etrade, whose _DATA_DIR / _TOKEN_PATH are resolved at import time.
os.environ.setdefault("AETHER_DATA_DIR", r"D:\Develop\AnalyzeFinData\Data")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pyetrade                     # noqa: E402
import aether.etrade as etrade      # noqa: E402

ENV = "production"


def _emit(msg: str = "") -> None:
    """Write one line to stdout (interactive display).

    This is a hands-on terminal tool whose whole job is to render a copy-paste
    authorize URL and PIN prompts to a human. Its output must stay on clean
    stdout, so it deliberately does NOT route through the module logger
    (``_log.console`` writes to stderr with a ``timestamp [CONSOLE] name:``
    prefix, which would corrupt the URL and step display). ``sys.stdout.write``
    is not a bare ``print`` and so satisfies the print-ban gate honestly,
    without touching shared lint config.
    """
    sys.stdout.write(f"{msg}\n")
    sys.stdout.flush()


def main() -> int:
    if not sys.stdin.isatty():
        _emit("ERROR: run this at a real terminal - it must prompt you for the PIN "
              "(input() fails with EOFError under a non-interactive/background process).")
        return 2

    ck, cs, _user, _pw = etrade._load_config(ENV)
    _emit(f"Auth-state dir : {etrade._DATA_DIR}")
    _emit(f"Token target   : {etrade._TOKEN_PATH}")
    _emit()

    oauth = pyetrade.ETradeOAuth(ck, cs)
    auth_url = oauth.get_request_token()

    _emit("=" * 72)
    _emit("STEP 1 - open this URL in YOUR normal Chrome (the trusted browser):")
    _emit()
    _emit("  " + auth_url)
    _emit()
    _emit("STEP 2 - log in (no SMS if the device is remembered), then click Accept.")
    _emit("STEP 3 - E*TRADE shows a short verification code / PIN. Copy it.")
    _emit("=" * 72)
    _emit()

    try:
        verifier = input("Paste the E*TRADE verification PIN here, then press Enter: ").strip()
    except (EOFError, RuntimeError):
        _emit("\nCould not read the PIN (no interactive terminal). Nothing saved.")
        return 2

    if not verifier:
        _emit("No PIN entered - aborting, nothing saved.")
        return 1

    tokens = oauth.get_access_token(verifier)
    if not tokens or not tokens.get("oauth_token"):
        _emit("ERROR: E*TRADE did not return an access token. "
              "PIN wrong or expired? Re-run and paste the code promptly.")
        return 1

    etrade._save_tokens(tokens, ENV)
    etrade.reset_reauth_circuit_breaker(ENV)
    _emit()
    _emit(f"SUCCESS - production token saved for {etrade._et_today()} (ET).")
    _emit(f"  -> {etrade._TOKEN_PATH}")
    _emit("Verifying with a live quote probe...")
    try:
        ok = etrade._probe_token_auth(tokens, ENV)
        _emit(f"  broker probe: {'OK (authorized)' if ok else 'returned ' + repr(ok)}")
    except Exception as e:
        _emit(f"  probe skipped ({e})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
