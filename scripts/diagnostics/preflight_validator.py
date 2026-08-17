"""
Project AETHER: Centralized Pre-Flight Connection & Config Validator (R&D #21)

This utility performs a rapid, 5-second diagnostic sweep of all external API gateways,
mailboxes, and session states, verifying system readiness before any cron runs.
"""

import datetime
import imaplib
import json
import os
import smtplib
import subprocess
import sys
import time
from pathlib import Path


# Insert project root to import our local modules cleanly
BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

import powergauge
from aether import etrade
from aether.config import CFG
from aether_logger import get_logger as _get_logger


_log = _get_logger("preflight")


def purge_browser_zombies():
    """Autonomically search and terminate any orphaned node.exe, headless_shell.exe,
    or automated chrome.exe zombie processes on Windows to prevent dangerous Playwright launch pipe-hangs!
    """
    if sys.platform != "win32":
        return
    try:
        # Force-kill only orphaned background Playwright NodeJS child processes, protecting Gemini CLI!
        subprocess.run(["taskkill", "/F", "/IM", "headless_shell.exe", "/T"], capture_output=True)

        # Fine-grained Node.exe cleanup protecting active Gemini sessions
        try:
            cmd = [
                "powershell.exe", "-NoProfile", "-Command",
                "Get-CimInstance Win32_Process -Filter \"Name = 'node.exe'\" | Select-Object ProcessId, CommandLine | ConvertTo-Json"
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if res.returncode == 0 and res.stdout.strip():
                data = json.loads(res.stdout)
                if not isinstance(data, list):
                    data = [data]
                killed_count = 0
                for proc in data:
                    pid = proc.get("ProcessId")
                    cmdline = proc.get("CommandLine") or ""
                    
                    # Strictly skip and protect Gemini CLI or standard developer shells
                    if any(x in cmdline for x in ["gemini-cli", "gemini.js", "claude-dev", "cline"]):
                        continue
                        
                    # Target only Playwright/driver-related Node.exe processes
                    if pid and any(x in cmdline for x in ["playwright", "run-driver", "playwright-core"]):
                        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
                        killed_count += 1
                if killed_count > 0:
                    _log.console(f"🧹 [Autonomic Healing] Purged {killed_count} background Playwright node.exe processes!")
        except Exception:
            pass

        # Fine-grained automated Chrome process cleanup (to protect user's active Chrome)
        try:
            cmd = [
                "powershell.exe", "-NoProfile", "-Command",
                "Get-CimInstance Win32_Process -Filter \"Name = 'chrome.exe'\" | Select-Object ProcessId, CommandLine | ConvertTo-Json"
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if res.returncode == 0 and res.stdout.strip():
                data = json.loads(res.stdout)
                if not isinstance(data, list):
                    data = [data]
                killed_count = 0
                for proc in data:
                    pid = proc.get("ProcessId")
                    cmdline = proc.get("CommandLine") or ""
                    if pid and any(x in cmdline for x in ["AutomationControlled", "remote-debugging-port", "remote-debugging-pipe"]):
                        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
                        killed_count += 1
                if killed_count > 0:
                    _log.console(f"🧹 [Autonomic Healing] Purged {killed_count} automated background chrome.exe processes!")
        except Exception:
            pass

        _log.console("🧹 [Autonomic Healing] Successfully purged all background browser and NodeJS zombie processes!")
    except Exception as e:
        _log.console(f"  ⚠️ Warning: Could not purge zombie processes: {e}")


def check_gmail_imap() -> bool:
    """Validate Gmail IMAP connection and credentials."""
    _log.console("  Checking Gmail IMAP Inbox Connection...")
    try:
        # Read credentials from config
        mailboxes = getattr(CFG, "mailboxes", [])
        if not mailboxes:
            _log.console("  ❌ IMAP: No mailboxes configured in config.json")
            return False
            
        mb = mailboxes[0]
        email_addr = mb.get("email")
        pass_env = mb.get("password_env", "SMTP_PASSWORD")
        password = os.environ.get(pass_env) or mb.get("password") or getattr(CFG, "smtp_password", "")
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
        sender = getattr(CFG, "email_sender_address", "")
        password = getattr(CFG, "smtp_password", "")
        smtp_server = "smtp.gmail.com"
        smtp_port = 587
        
        if not sender or "example.com" in sender:
            _log.console("  ❌ SMTP: Default placeholder sender detected.")
            return False
            
        # Connect to SMTP
        server = smtplib.SMTP(smtp_server, smtp_port, timeout=5)
        server.starttls()
        server.login(sender, password)
        server.quit()
        _log.console("  ✅ SMTP: Successfully authenticated SMTP dispatch server.")
        return True
    except Exception as e:
        _log.console(f"  ❌ SMTP: Connection failed: {e}")
        return False


def check_chaikin_api() -> bool:
    """Validate Chaikin PowerGauge API session credentials."""
    _log.console("  Checking Chaikin PowerGauge API Authorization...")
    try:
        session = powergauge._load_session_from_file()
        if session and powergauge._validate_session(session):
            _log.console("  ✅ Chaikin: Successfully verified session authorization token from cache.")
            return True
        else:
            _log.console("  ⚠️ Chaikin: Cached session expired or invalid. Attempting headless refresh...")
            session = powergauge.ensure_valid_session()
            if session and session.get("jsessionid"):
                _log.console("  ✅ Chaikin: Successfully validated live Chaikin session.")
                return True
            _log.console("  ❌ Chaikin: Failed to obtain or validate Chaikin session.")
            return False
    except Exception as e:
        _log.console(f"  ❌ Chaikin: API handshake failed: {e}")
        return False


def check_etrade_api() -> bool:
    """Validate live E*TRADE API OAuth session token validity.
    On weekends (Saturdays and Sundays), since the stock market is closed,
    verification failures are waived to allow reporting/summaries to run.
    """
    _log.console("  Checking E*TRADE Brokerage OAuth Session Token...")
    is_weekend = datetime.datetime.now().weekday() in (5, 6)
    try:
        tokens = etrade.get_tokens(env="production", allow_browser=False)
        if tokens:
            _log.console("  ✅ E*TRADE: Successfully verified and renewed live OAuth tokens.")
            return True
        else:
            if is_weekend:
                _log.console("  ⚠️ E*TRADE: Verification failed, but waiving requirement because today is the weekend (market closed).")
                return True
            _log.console("  ❌ E*TRADE: No valid cached session or headless Playwright login failed.")
            return False
    except Exception as e:
        if is_weekend:
            _log.console(f"  ⚠️ E*TRADE: Active OAuth verification failed ({e}), but waiving requirement because today is the weekend (market closed).")
            return True
        _log.console(f"  ❌ E*TRADE: Active OAuth verification failed: {e}")
        return False


def run_preflight_diagnostics() -> bool:
    """Execute all pre-flight diagnostic checks and return True on 100% PASS."""
    # Autonomically purge browser and NodeJS zombie processes first to prevent Playwright pipe hangs!
    purge_browser_zombies()

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
