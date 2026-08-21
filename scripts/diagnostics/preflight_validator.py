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

import notify
import powergauge
from aether import etrade
from aether.config import CFG
from aether_logger import get_logger as _get_logger


_log = _get_logger("preflight")


def purge_browser_zombies():
    """Terminate orphaned node.exe, headless_shell.exe, or automated chrome.exe processes
    on Windows so a stale Playwright child can't hang the next browser launch on a pipe.
    Skips developer/Gemini CLI processes so only automation zombies are killed.
    """
    if sys.platform != "win32":
        return
    try:
        # Kill only orphaned Playwright headless-shell children; leave the CLI host alone.
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
                    _log.console(f"🧹 Purged {killed_count} orphaned Playwright node.exe process(es).")
        except Exception as e:
            _log.warning(f"node.exe zombie sweep skipped (non-fatal): {e}")

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
                    _log.console(f"🧹 Purged {killed_count} automated background chrome.exe process(es).")
        except Exception as e:
            _log.warning(f"chrome.exe zombie sweep skipped (non-fatal): {e}")

        _log.console("🧹 Browser/NodeJS zombie sweep complete.")
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


def check_file_and_directory_integrity(base_dir: Path = BASE_DIR) -> tuple[bool, list[str]]:
    """Verify that all required folders and files exist on disk."""
    _log.console("  Checking File & Directory Integrity...")
    missing = []

    required_dirs = [
        base_dir / "Data",
        base_dir / "Data" / "Backup",
        base_dir / "Data" / "Symbol_full"
    ]
    required_files = [
        base_dir / "config.json",
        base_dir / "Data" / "state_of_the_day.xlsx"
    ]
    
    for d in required_dirs:
        if not d.exists():
            missing.append(f"Directory: {d.name}")
            
    for f in required_files:
        if not f.exists():
            missing.append(f"File: {f.name}")
            
    if missing:
        _log.console(f"  ❌ INTEGRITY: Missed files/dirs: {', '.join(missing)}")
        return False, missing
    _log.console("  ✅ INTEGRITY: All required directories and files are present on disk.")
    return True, []


def check_active_locks(base_dir: Path = BASE_DIR) -> tuple[bool, list[str]]:
    """Check for active or stale file locks that would block execution."""
    _log.console("  Checking Active Process & File Locks...")
    locks = []

    pipeline_lock = base_dir / "Data" / "pipeline_run.lock"
    rapidapi_lock = base_dir / "Data" / "rapidapi.lock"
    xlsx_file = base_dir / "Data" / "state_of_the_day.xlsx"

    if pipeline_lock.exists():
        locks.append("Pipeline Active Lock (pipeline_run.lock)")

    if rapidapi_lock.exists():
        locks.append("RapidAPI Active Lock (rapidapi.lock)")

    # Detect an exclusive lock (e.g. the workbook open in Excel) without mutating
    # the file: renaming a path to itself raises PermissionError/OSError when the
    # OS holds a write/delete lock, and is a no-op otherwise.
    if xlsx_file.exists():
        try:
            os.rename(str(xlsx_file), str(xlsx_file))
        except (PermissionError, OSError):
            locks.append("Excel File Lock (state_of_the_day.xlsx is open/locked)")
            
    if locks:
        _log.console(f"  ⚠️ LOCKS: Found active/stale locks: {', '.join(locks)}")
        return False, locks
    _log.console("  ✅ LOCKS: No active process or spreadsheet locks detected.")
    return True, []


def send_preflight_email(checks, missing_items, active_locks, duration, all_ok):
    """Compile and dispatch an HTML status briefing email to the user.

    `checks` is the single ordered roster of (label, ok, kind) tuples shared with
    the console summary, so the email table and the console can never drift. `all_ok`
    is the overall verdict computed by the caller, so the pass/fail decision has
    exactly one source of truth."""
    _log.console("  Sending pre-flight status email...")
    try:
        today = datetime.date.today().strftime("%Y-%m-%d")
        subject = f"🔔 AETHER Pre-Flight Status Briefing: {today}"

        def _badge(ok):
            return '<span style="color: #2ea043; font-weight: bold;">[PASS]</span>' if ok else '<span style="color: #f85149; font-weight: bold;">[FAIL]</span>'

        def _lock_badge(ok):
            return '<span style="color: #2ea043; font-weight: bold;">[CLEAN]</span>' if ok else '<span style="color: #db6d28; font-weight: bold;">[LOCKED]</span>'

        rows = ""
        for i, (label, ok, kind) in enumerate(checks, 1):
            badge = _lock_badge(ok) if kind == "lock" else _badge(ok)
            rows += (
                '<tr style="border-bottom: 1px solid #21262d;">'
                f'<td style="padding: 10px 0; color: #8b949e;">[{i}] {label}</td>'
                f'<td style="padding: 10px 0; text-align: right;">{badge}</td>'
                '</tr>'
            )

        html = f"""
        <div style="font-family: monospace; background-color: #0d1117; color: #c9d1d9; border: 1px solid #30363d; border-radius: 8px; padding: 25px; max-width: 650px; margin: 20px auto; box-shadow: 0 4px 6px rgba(0,0,0,0.15);">
            <h2 style="color: #58a6ff; border-bottom: 1px solid #30363d; padding-bottom: 12px; margin-top: 0; font-size: 18px; font-weight: bold; letter-spacing: 0.5px;">
                🔔 AETHER Pre-Flight Status Briefing
            </h2>
            <p style="font-size: 13px; color: #8b949e; margin-bottom: 20px;">
                Generated on: {datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")}<br>
                Check Duration: {duration:.2f}s
            </p>

            <table style="width: 100%; border-collapse: collapse; font-size: 13px; margin-bottom: 20px;">
                {rows}
            </table>
        """
        
        if missing_items:
            html += f"""
            <div style="background-color: rgba(248,81,73,0.1); border: 1px solid #f85149; border-radius: 6px; padding: 12px; margin-bottom: 20px; font-size: 12px; color: #ff7b72;">
                ⚠️ <b>Missed Required Files/Directories:</b>
                <ul style="margin: 5px 0 0 15px; padding: 0;">
                    {"".join(f"<li>{x}</li>" for x in missing_items)}
                </ul>
            </div>
            """
            
        if active_locks:
            html += f"""
            <div style="background-color: rgba(219,109,40,0.1); border: 1px solid #db6d28; border-radius: 6px; padding: 12px; margin-bottom: 20px; font-size: 12px; color: #f0883e;">
                ⚠️ <b>Active Process/Workbook Locks:</b>
                <ul style="margin: 5px 0 0 15px; padding: 0;">
                    {"".join(f"<li>{x}</li>" for x in active_locks)}
                </ul>
            </div>
            """
            
        if all_ok:
            html += """
            <div style="background-color: rgba(46,160,67,0.15); border: 1px solid #2ea043; border-radius: 6px; padding: 15px; text-align: center; color: #56d364; font-size: 13px; font-weight: bold;">
                ☀️ [SUCCESS] All pre-flight checks passed. System ready for tomorrow's run.
            </div>
            """
        else:
            html += """
            <div style="background-color: rgba(248,81,73,0.15); border: 1px solid #f85149; border-radius: 6px; padding: 15px; text-align: center; color: #ff7b72; font-size: 13px; font-weight: bold;">
                🚨 [ALERT] One or more pre-flight check blocks exist. Action required.
            </div>
            """
            
        html += "</div>"
        
        notify.send_email(subject, html, is_html=True)
        _log.console("  ✅ Email: Successfully sent pre-flight status email.")
    except Exception as e:
        _log.error(f"  ❌ Email: Failed to dispatch status briefing: {e}")


def run_preflight_diagnostics() -> bool:
    """Execute all pre-flight diagnostic checks and return True only if every check passes."""
    # Purge browser/NodeJS zombies first so a stale process can't hang a later Playwright launch.
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
    integrity_ok, missing_items = check_file_and_directory_integrity()
    locks_ok, active_locks = check_active_locks()
    
    duration = time.time() - start_time

    # Single source of truth for the check roster: (label, result, kind). "lock"
    # renders CLEAN/LOCKED, everything else PASS/FAIL. Both the console summary
    # below and the email table render from this one list, so they cannot drift.
    checks = [
        ("Gmail IMAP",                  imap_ok,      "conn"),
        ("Gmail SMTP Dispatch",         smtp_ok,      "conn"),
        ("Chaikin PowerGauge API",      chaikin_ok,   "conn"),
        ("E*TRADE Brokerage OAuth",     etrade_ok,    "conn"),
        ("File & Directory Integrity",  integrity_ok, "conn"),
        ("Active Process & File Locks", locks_ok,     "lock"),
    ]

    _log.console("=" * 70)
    _log.console(f"PRE-FLIGHT DIAGNOSTIC SUMMARY (Duration: {duration:.2f}s)")
    _log.console("-" * 70)
    for i, (label, ok, kind) in enumerate(checks, 1):
        word = ("CLEAN" if ok else "LOCKED") if kind == "lock" else ("PASS" if ok else "FAIL")
        _log.console(f"  [{i}] {label:<28}: {word}")
    _log.console("=" * 70)

    all_ok = all(ok for _, ok, _ in checks)

    # Send email status if the --email flag is set
    if "--email" in sys.argv:
        send_preflight_email(checks, missing_items, active_locks, duration, all_ok)
        
    if all_ok:
        _log.console("☀️ [PRE-FLIGHT SUCCESS] All external API and email gateways are online.")
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
