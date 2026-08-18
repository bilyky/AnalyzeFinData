"""
Project AETHER: Zero-Trust Manual E*TRADE Token Sync Utility

Allows exchanging an E*TRADE verifier code for live access tokens
even if the terminal is non-interactive or the browser cannot load locally due to IP blocks.
"""

import argparse
import json
import os
import sys
from pathlib import Path


# Insert project root to import local modules cleanly
BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

import pyetrade
from requests_oauthlib import OAuth1Session

from aether import etrade
from aether_logger import get_logger as _get_logger


_log = _get_logger("manual_sync")
TEMP_PATH = os.path.join(BASE_DIR, "Data", "etrade_temp_request.json")


def step1_generate():
    _log.console("Generating fresh E*TRADE request token...")
    ck, cs, _, _ = etrade._load_config("production")
    oa = pyetrade.ETradeOAuth(ck, cs)
    auth_url = oa.get_request_token()
    
    # Save the request token and secret to disk
    temp_data = {
        "key": oa.resource_owner_key,
        "secret": oa.session.auth.client.resource_owner_secret
    }
    os.makedirs(os.path.dirname(TEMP_PATH), exist_ok=True)
    with open(TEMP_PATH, "w") as f:
        json.dump(temp_data, f)
        
    _log.console("=" * 75)
    _log.console("👉 OPEN THIS URL ON YOUR MOBILE PHONE (ensure Phone Wi-Fi is OFF):")
    _log.console(auth_url)
    _log.console("=" * 75)
    _log.console("Log in on your phone, click Accept, and paste the 5-character code back here using:")
    _log.console("  python scripts/diagnostics/manual_etrade_sync.py --code <YOUR_CODE>")
    _log.console("=" * 75)


def step2_exchange(code):
    _log.console(f"Attempting token exchange for verifier code: {code}")
    if not os.path.exists(TEMP_PATH):
        _log.console("❌ Error: Temporary request token file not found. Please run --generate first!")
        sys.exit(1)
        
    with open(TEMP_PATH) as f:
        req_data = json.load(f)
        
    ck, cs, _, _ = etrade._load_config("production")
    oa = pyetrade.ETradeOAuth(ck, cs)
    
    # Reconstruct the session state
    oa.resource_owner_key = req_data["key"]
    oa.session = OAuth1Session(
        client_key=ck,
        client_secret=cs,
        resource_owner_key=req_data["key"],
        resource_owner_secret=req_data["secret"],
        callback_uri="oob"
    )
    
    try:
        tokens = oa.get_access_token(code)
        etrade._save_tokens(tokens, "production")
        _log.console("✅ SUCCESS! E*TRADE live access tokens have been cached and saved to disk!")
        
        # Actively verify the new tokens immediately against E*TRADE's public server clock!
        is_valid = etrade._test_tokens_valid(tokens, "production")
        if is_valid:
            _log.console("☀️ [VERIFICATION PASS] Your new tokens are 100% VALID and accepted by E*TRADE!")
        else:
            _log.console("⚠️ [VERIFICATION FAIL] E*TRADE returned 401 Unauthorized for the new tokens.")
            # Let's perform a raw debug query to print the response body
            try:
                url = "https://api.etrade.com/v1/market/clock.json"
                session = OAuth1Session(ck, cs, tokens["oauth_token"], tokens["oauth_token_secret"])
                resp = session.get(url)
                _log.console(f"  Debug Query - HTTP Status: {resp.status_code}")
                _log.console(f"  Debug Query - Response Body: {resp.text}")
            except Exception as de:
                _log.console(f"  Debug Query failed: {de}")
        
        # Clean up temp file
        try:
            os.remove(TEMP_PATH)
        except Exception:
            pass
            
        # Reset cooldown and failures
        for file in ["etrade_cooldown.lock", "etrade_fail_state.json"]:
            try:
                os.unlink(os.path.join(BASE_DIR, "Data", file))
            except Exception:
                pass
    except Exception as e:
        _log.console(f"❌ Error: Token exchange failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AETHER Manual E*TRADE Token Sync")
    parser.add_argument("--generate", action="store_true", help="Generate a request token and mobile login URL")
    parser.add_argument("--code", type=str, help="Complete token exchange using the 5-character verifier code")
    args = parser.parse_args()
    
    if args.generate:
        step1_generate()
    elif args.code:
        step2_exchange(args.code)
    else:
        parser.print_help()
