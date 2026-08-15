"""
Project AETHER: Centralized Pre-Flight Connection & Config Validator (R&D #21)

This utility performs a rapid, 5-second diagnostic sweep of all external API gateways,
mailboxes, and session states, verifying system readiness before any cron runs.
"""

import sys
import os
import time
from pathlib import Path

# Insert project root to import our local modules cleanly
BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from aether_logger import get_logger as _get_logger
from aether.config import CFG

_log = _get_logger("preflight")


def check_gmail_imap() -> bool:
    """Validate Gmail IMAP connection and credentials."""
    _log.console("  Checking Gmail IMAP Inbox Connection...")
    try:
        import imaplib
        # Read credentials from config
        mailboxes = getattr(CFG, "mailboxes", [])
        if not mailboxes:
            _log.console("  ❌ IMAP: No mailboxes configured in config.json")
            return False
            
        mb = mailboxes[0]
        email_addr = mb.get("email")
        password = mb.get("password")
        imap_server = mb.get("imap_server", "imap.gmail.com")
        
        if not email_addr or "example.com" in email_addr:
            _log.console("  ❌ IMAP: Default placeholder email detected.")
            return False
            
        # Connect to IMAP
        mail = imaplib.IMAP4_SSL(imap_server)
        mail.login(email_addr, password)
        mail.logout()
        _log.console(f"  ✅ IMAP: Successfully logged into mailbox: {email_addr}")
        return True
    except Exception as e:
        _log.console(f"  ❌ IMAP: Connection failed: {e}")
        return False


def check_gmail_smtp() -> bool:
    """Validate Gmail SMTP connection and delivery endpoints."""
    _log.console("  Checking Gmail SMTP Dispatch Connection...")
    try:
        import smtplib
        email_cfg = getattr(CFG, "email", {})
        sender = email_cfg.get("sender")
        password = email_cfg.get("password")
        smtp_server = email_cfg.get("smtp_server", "smtp.gmail.com")
        smtp_port = int(email_cfg.get("smtp_port", 587))
        
        if not sender or "example.com" in sender:
            _log.console("  ❌ SMTP: Default placeholder sender detected.")
            return False
            
        # Connect to SMTP
        server = smtplib.SMTP(smtp_server, smtp_port, timeout=5)
        server.starttls()
        server.login(sender, password)
        server.quit()
        _log.console(f"  ✅ SMTP: Successfully authenticated SMTP dispatch server.")
        return True
    except Exception as e:
        _log.console(f"  ❌ SMTP: Connection failed: {e}")
        return False


def check_chaikin_api() -> bool:
    """Validate Chaikin PowerGauge API session credentials."""
    _log.console("  Checking Chaikin PowerGauge API Authorization...")
    try:
        import urllib.request
        import json
        
        email = getattr(CFG, "chaikin_email", "")
        pwd = getattr(CFG, "chaikin_password", "")
        
        if not email or "example.com" in email:
            _log.console("  ❌ Chaikin: Default placeholder email detected.")
            return False
            
        # Test auth endpoint
        url = "https://members-backend.chaikinanalytics.com/api/v1/auth/login"
        payload = json.dumps({"email": email, "password": pwd}).encode("utf-8")
        req = urllib.request.Request(
            url, data=payload, 
            headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
            method="POST"
        )
        # Suppress SSL context warning for raw urllib pings
        import ssl
        context = ssl._create_unverified_context()
        
        with urllib.request.urlopen(req, context=context, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if resp.status == 200 and data.get("token"):
                _log.console("  ✅ Chaikin: Successfully verified session authorization token.")
                return True
            else:
                _log.console(f"  ❌ Chaikin: Unexpected API payload response: {data}")
                return False
    except Exception as e:
        _log.console(f"  ❌ Chaikin: API handshake failed: {e}")
        return False


def check_etrade_api() -> bool:
    """Validate live E*TRADE API OAuth session token validity."""
    _log.console("  Checking E*TRADE Brokerage OAuth Session Token...")
    try:
        from aether import etrade
        tokens = etrade.get_tokens(env="production", allow_browser=False)
        if tokens:
            _log.console("  ✅ E*TRADE: Successfully verified and renewed live OAuth tokens.")
            return True
        else:
            _log.console("  ❌ E*TRADE: No valid cached session or headless Playwright login failed.")
            return False
    except Exception as e:
        _log.console(f"  ❌ E*TRADE: Active OAuth verification failed: {e}")
        return False


def run_preflight_diagnostics() -> bool:
    """Execute all pre-flight diagnostic checks and return True on 100% PASS."""
    _log.console("=" * 70)
    _log.console("AETHER: RUNNING PRE-FLIGHT CONNECTION & ADVISORY DIAGNOSTICS...")
    _log.console("=" * 70)
    
    start_time = time.time()
    
    # ── Execute Checks ──
    imap_ok   = check_gmail_imap()
    smtp_ok   = check_gmail_smtp()
    chaikin_ok = check_chaikin_api()
    etrade_ok  = check_etrade_api()
    
    duration = time.time() - start_time
    _log.console("=" * 70)
    _log.console(f"PRE-FLIGHT DIAGNOSTIC SUMMARY (Duration: {duration:.2f}s)")
    _log.console("-" * 70)
    
    _log.console(f"  [1] Gmail IMAP Inbox     : {'PASS' if imap_ok else 'FAIL'}")
    _log.console(f"  [2] Gmail SMTP Dispatch  : {'PASS' if smtp_ok else 'FAIL'}")
    _log.console(f"  [3] Chaikin PowerGauge   : {'PASS' if chaikin_ok else 'FAIL'}")
    _log.console(f"  [4] E*TRADE OAuth Feed   : {'PASS' if etrade_ok else 'FAIL'}")
    _log.console("=" * 70)
    
    all_ok = imap_ok and smtp_ok and chaikin_ok and etrade_ok
    if all_ok:
        _log.console("☀️ [PRE-FLIGHT SUCCESS] All external API and email gateways are 100% ONLINE.")
        _log.console("=" * 70)
        return True
    else:
        _log.console("🚨 [PRE-FLIGHT FAILURE] One or more critical system gateways are OFFLINE!")
        _log.console("  System will defensively halt to prevent locked-file or duplicate run leaks.")
        _log.console("=" * 70)
        return False


if __name__ == "__main__":
    success = run_preflight_diagnostics()
    sys.exit(0 if success else 1)
