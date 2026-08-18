import os
import sys
import atexit
import console_safe
import datetime
import pytz
import subprocess
import re
import ctypes
import json
import notify
import etrade
import powergauge
from pathlib import Path
from aether_logger import get_logger as _get_logger

_log = _get_logger("watchdog")

# Windows CP1252 console fallback (bug-fix workaround, not a feature): must run
# before the first non-ASCII print. Reduces, not eliminates, cp1252 crashes in
# headless runs. See AETHER_REFERENCE.md.
console_safe.install()

# --- CONFIGURATION ---
BASE_DIR = Path(__file__).resolve().parent
LOG_FILES = [
    BASE_DIR / "Data" / "autonomous_run.log",
    BASE_DIR / "daily_task.log",
]
AETHER_JSONL = BASE_DIR / "Data" / "logs" / "aether.jsonl"
XLSX_FILE = BASE_DIR / "Data" / "state_of_the_day.xlsx"
TASKS = ["AnalyzeFinData_Morning", "AnalyzeFinData_AI_Game", "AnalyzeFinData_AI_Summary", "AnalyzeFinData_Evening"]
SELF_HEAL_LOCK = BASE_DIR / "Data" / "self_healing.lock"

python_exe = sys.executable
_TASK_DEFS = {
    "AnalyzeFinData_Morning":  (f"'{python_exe}' '{BASE_DIR / 'autonomous_pipeline.py'}'", "daily", "05:30"),
    "AnalyzeFinData_Evening":  (f"'{python_exe}' '{BASE_DIR / 'daily_task.py'}'",           "daily", "17:00"),
    "AnalyzeFinData_AI_Game":  (f"'{python_exe}' '{BASE_DIR / 'ai_portfolio_game.py'}' --run", "daily", "07:00"),
    "AnalyzeFinData_AI_Summary": (f"'{python_exe}' '{BASE_DIR / 'ai_portfolio_game.py'}' --summary", "daily", "18:00"),
    "Project_AETHER_Watchdog": (f"'{python_exe}' '{BASE_DIR / 'watchdog.py'}'",              "hourly", None),
}

SELF_HEAL_PROMPT_FILE = BASE_DIR / "Data" / "self_healing_prompt.txt"

# --- Agnostic AI Self-Healing Tool Configuration ---
# Supports any AI CLI or custom scripts (Gemini CLI, Claude, local Codex, custom wrappers).
# Defaults to npx @google/gemini-cli but can be overridden globally via environment variables.
# Placeholders: {prompt} (inline text) or {prompt_file} (safe text file path, highly recommended for Windows).
_CLAUDE_EXE = os.path.expandvars(r"%USERPROFILE%\.gnai\claude\claude.exe")
_DEFAULT_HEALER = (
    f'"{_CLAUDE_EXE}" --allowedTools "Bash,Read,Edit,Write,Glob,Grep"'
    ' --approval-mode acceptEdits'
    ' -p "{prompt_file}"'
)
HEALER_CMD_TEMPLATE = os.environ.get("AETHER_HEALER_CMD", _DEFAULT_HEALER)

def _within_window(ts_str: str, now: datetime.datetime, window_seconds: int = 3600) -> bool:
    """True if the ISO-like timestamp string is within window_seconds of now."""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            t = datetime.datetime.strptime(ts_str[:19], fmt)
            return (now - t).total_seconds() < window_seconds
        except ValueError:
            continue
    return False


def _check_plain_logs(now: datetime.datetime) -> list[str]:
    """Scan plain-text log files for error keywords in the last hour."""
    errors = []
    _error_words = {"ERROR", "FAILED", "FATAL", "CRASH", "TRACEBACK",
                    "UNBOUNDLOCALERROR", "UNICODEENCODEERROR", "SYMBOLOGY ERROR",
                    "PORTFOLIO ERROR"}
    _skip_words = {"0 ERRORS", "NO ERRORS"}
    for log_path in LOG_FILES:
        if not log_path.exists():
            continue
        try:
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            for line in lines[-50:]:
                upper = line.upper()
                if "OHLCV:" in upper:
                    continue
                if not any(w in upper for w in _error_words):
                    continue
                if any(w in upper for w in _skip_words):
                    continue
                # Try to parse the timestamp from [YYYY-MM-DD HH:MM:SS] prefix
                ts_raw = line.split("]")[0].strip("[")
                if _within_window(ts_raw, now):
                    errors.append(f"[{log_path.name}] {line.strip()}")
        except Exception as e:
            _log.warning("Failed to read log file", extra={"path": str(log_path), "error": str(e)})
    return errors


def _check_structured_log(now: datetime.datetime) -> list[str]:
    """Scan aether.jsonl for ERROR entries in the last hour."""
    errors = []
    if not AETHER_JSONL.exists():
        return errors
    try:
        with open(AETHER_JSONL, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        for line in lines[-200:]:  # recent enough window for structured log
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("level", "").upper() != "ERROR":
                continue
            if not _within_window(entry.get("ts", ""), now):
                continue
            module = entry.get("module", "")
            msg = entry.get("msg", "")
            extra = entry.get("extra", {})
            exc = entry.get("exc", "")
            detail = f" | {json.dumps(extra)}" if extra else ""
            if exc:
                # Include just the last line of the traceback
                detail += f" | {exc.strip().splitlines()[-1]}"
            errors.append(f"[{module}] {msg}{detail}")
    except Exception as e:
        _log.warning("Failed to scan structured log", extra={"error": str(e)})
    return errors


def check_logs():
    """Audit all AETHER logs (plain text + structured JSONL) for errors in the last hour."""
    now = datetime.datetime.now()
    errors = _check_plain_logs(now) + _check_structured_log(now)
    if errors:
        _log.warning("check_logs found issues", extra={"count": len(errors)})
    return errors

def extract_latest_traceback():
    """Extract the most recent multi-line traceback from the autonomous log."""
    log_path = BASE_DIR / "Data" / "autonomous_run.log"
    if not log_path.exists():
        return ""
    
    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        
        # Look for the last "failed (Exit 1):" block
        parts = content.split("failed (Exit ")
        if len(parts) > 1:
            last_failure = parts[-1]
            return "AI Game failed (Exit " + last_failure[:1500] # Limit size safely
    except Exception as e:
        _log.error(f"Failed to extract traceback: {e}")
    return ""

def trigger_ai_self_healing(traceback):
    """Headlessly trigger the configured AI CLI Agent synchronously to self-heal the python codebase on the fly."""
    if SELF_HEAL_LOCK.exists():
        _log.console("  [Healer] Circuit breaker active (self_healing.lock found). Skipping AI trigger.")
        return False, "Circuit breaker active. Code needs manual review.", ""
    
    # Create the lock to prevent recursive self-healing loops
    with open(SELF_HEAL_LOCK, "w", encoding="utf-8") as f:
        f.write(f"Active since: {datetime.datetime.now()}\nTraceback: {traceback[:200]}\n")
        
    _log.error("🧠 [AETHER BRAIN] CRITICAL ERROR DETECTED. ACTIVATING SYNCHRONOUS SELF-HEALER...")
    
    _prompt_template = BASE_DIR / "prompts" / "self_healing.md"
    if _prompt_template.exists():
        prompt = _prompt_template.read_text(encoding="utf-8").replace("{traceback}", traceback)
    else:
        prompt = f"[AETHER SELF-HEALER] Pipeline crashed. Traceback:\n{traceback}\n\nRead the failing file, diagnose the root cause, apply a minimal fix, run python -m unittest discover tests, commit only if all tests pass."
    
    try:
        # Write the prompt to a safe text file to avoid Windows escaping issues
        with open(SELF_HEAL_PROMPT_FILE, "w", encoding="utf-8") as f:
            f.write(prompt)
            
        cmd = HEALER_CMD_TEMPLATE.format(
            prompt=prompt.replace('"', '\\"').replace('\n', ' '),
            prompt_file=str(SELF_HEAL_PROMPT_FILE)
        )
        _log.console(f"🚀 [AETHER BRAIN] Dispatching self-healing command (prompt written to file)")
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=300,
            errors="ignore"
        )
        
        # Merge stdout and stderr for full visibility
        console_log = f"STDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}"
        
        if result.returncode == 0:
            _log.console("✅ [AETHER BRAIN] Self-healing process completed successfully.")
            return True, "Self-healer executed successfully.", console_log
        else:
            _log.error(f"❌ [AETHER BRAIN] Self-healer exited with code {result.returncode}.")
            return False, f"Self-healer failed with exit code {result.returncode}.", console_log
            
    except subprocess.TimeoutExpired:
        _log.error("❌ [AETHER BRAIN] Self-healing process timed out (5 minute limit reached).")
        return False, "Self-healing process timed out (5 min limit reached).", "TIMEOUT"
    except Exception as e:
        _log.error(f"❌ Failed to run Self-Healer: {e}")
        # Clean up files if we fail to spawn
        if SELF_HEAL_LOCK.exists(): SELF_HEAL_LOCK.unlink()
        if SELF_HEAL_PROMPT_FILE.exists(): SELF_HEAL_PROMPT_FILE.unlink()
        return False, f"Execution failure: {e}", str(e)

def check_task_scheduler():
    """Verify all AETHER tasks are present and active."""
    missing = []
    for task in TASKS:
        # Use absolute task path starting with backslash to prevent folder-relative lookup failures
        abs_task = f"\\{task}" if not task.startswith("\\") else task
        try:
            result = subprocess.run(["schtasks", "/query", "/tn", abs_task], capture_output=True, text=True, errors="replace")
            if result.returncode != 0:
                # Avoid assuming a task is missing on environment failures, access issues,
                # or DLL initialization crashes (e.g. exit code 3221225794 / 0xC0000142).
                output = f"{result.stdout or ''} {result.stderr or ''}".lower()
                if "cannot find" in output or "not find" in output:
                    missing.append(task)
        except OSError:
            missing.append(task)
    return missing

def purge_stray_tasks():
    """Detect Task Scheduler entries in our namespace that are not in the TASKS white-list.

    Log-only by default: deletion is DESTRUCTIVE and can catch legitimate ad-hoc tasks
    (e.g. a *_Test entry), so a stray is only auto-deleted when AETHER_PURGE_STRAY_TASKS=1
    is explicitly set. Otherwise it is reported and left in place.
    """
    do_delete = os.environ.get("AETHER_PURGE_STRAY_TASKS", "").strip() == "1"
    try:
        # Query all scheduled tasks in LIST format
        result = subprocess.run(["schtasks", "/query", "/fo", "LIST"], capture_output=True, text=True, errors="replace")
        if result.returncode == 0:
            # Extract all task names matching the AETHER / AnalyzeFinData prefix
            # TaskName contains the path (e.g. \AnalyzeFinData_Morning or \AnalyzeFinData_Morning_Test)
            all_tasks = re.findall(r"TaskName:\s+\\*(AnalyzeFinData_\w+|Project_AETHER_\w+)", result.stdout, re.IGNORECASE)
            
            # Filter and deduplicate
            stray_tasks = []
            for t in set(all_tasks):
                # If it carries our namespace but is not in our official white-list (TASKS or Project_AETHER_Watchdog)
                if t not in TASKS and t != "Project_AETHER_Watchdog":
                    stray_tasks.append(t)
            
            if stray_tasks:
                if not do_delete:
                    _log.warning(f"[Stray-Task] Detected {len(stray_tasks)} task(s) outside the production white-list "
                                 f"(log-only; set AETHER_PURGE_STRAY_TASKS=1 to delete): {stray_tasks}")
                else:
                    _log.warning(f"[Stray-Task] Purging {len(stray_tasks)} stray task(s) (AETHER_PURGE_STRAY_TASKS=1): {stray_tasks}")
                    for t in stray_tasks:
                        del_res = subprocess.run(["schtasks", "/delete", "/tn", f"\\{t}", "/f"], capture_output=True, text=True, errors="replace")
                        if del_res.returncode == 0:
                            _log.info(f"[Stray-Task] Deleted stray task '{t}'.")
                        else:
                            _log.error(f"[Stray-Task] Failed to delete '{t}' (rc={del_res.returncode}): {del_res.stderr.strip()}")
    except Exception as e:
        _log.error(f"Failed to execute stray task purge: {e}")

def check_data_freshness():
    """Ensure the workbook was updated in the last 24h."""
    if not XLSX_FILE.exists():
        return "CRITICAL: state_of_the_day.xlsx missing!"
    
    mtime = datetime.datetime.fromtimestamp(XLSX_FILE.stat().st_mtime)
    if (datetime.datetime.now() - mtime).total_seconds() > 90000: # ~25 hours
        return f"WARNING: Data is stale. Last updated: {mtime}"
    return None

def heal_tasks(missing_tasks, force=False):
    """Attempt to re-register tasks that have disappeared, failed, or need environment upgrades."""
    # Check for administrative privileges first to avoid UAC and privilege failures in background runs
    is_admin = False
    try:
        is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        pass

    if not is_admin:
        _log.warning("⚠️ Non-elevated context: skipping auto-healing of missing tasks to prevent privilege/UAC errors. "
                     f"Detected missing tasks: {', '.join(missing_tasks)}. "
                     "Please run an elevated command prompt as Administrator or run migrate_tasks_headless.py manually to register.")
        return

    try:
        run_as = os.getlogin()
    except Exception:
        run_as = os.environ.get("USERNAME") or os.environ.get("USER") or "SYSTEM"

    for task in (TASKS if force else missing_tasks):
        _log.console(f"🔧 Healing/Upgrading scheduled task: {task}")
        if task not in _TASK_DEFS:
            _log.warning(f"⚠️  No registration template for task: {task}")
            continue
        tr, sc, st = _TASK_DEFS[task]
        # Use absolute task path starting with backslash to prevent folder-relative registration failures
        abs_task = f"\\{task}" if not task.startswith("\\") else task
        args = ["schtasks", "/create", "/tn", abs_task, "/tr", tr, "/sc", sc, "/f", "/np", "/ru", run_as]
        if st:
            args += ["/st", st]
        try:
            result = subprocess.run(args, capture_output=True)
            if result.returncode == 0:
                _log.info(f"✅ Task {task} successfully registered with native UTF-8 environment.")
            else:
                _log.error(f"❌ schtasks failed for {task} (rc={result.returncode}): {result.stderr.decode(errors='replace').strip()}")
        except Exception as e:
            _log.error(f"❌ Failed to heal {task}: {e}")

def kill_ghost_processes():
    """Kill hung background python processes that might be locking resources."""
    _log.console("🧹 Cleaning up hung background python processes...")
    try:
        # We strictly terminate background python processes holding AnalyzeFinData resources.
        # We NEVER forcefully terminate excel.exe automatically because the user may have
        # other open, unsaved, and highly important unrelated spreadsheets!
        subprocess.run(["powershell", "Get-Process | Where-Object { $_.Name -match 'python' -and $_.CommandLine -match 'AnalyzeFinData' } | Stop-Process -Force"], capture_output=True)
    except:
        pass

def is_market_hours() -> bool:
    """Return True if current time is within active US equity market hours (6:30 AM - 1:15 PM PST, weekdays)."""
    try:
        tz_la = pytz.timezone("America/Los_Angeles")
        now_la = datetime.datetime.now(tz_la)
    except Exception:
        now_la = datetime.datetime.now()
        
    # Check weekday (Saturday=5, Sunday=6)
    if now_la.weekday() in (5, 6):
        return False
        
    # Check time: 6:30 AM - 1:15 PM Pacific
    # 6:30 AM = 390 min; 1:15 PM = 795 min
    current_minutes = now_la.hour * 60 + now_la.minute
    if 390 <= current_minutes <= 795:
        return True
    return False

def sync_data_folder() -> bool:
    """Sync the local Data folder to the backup Z: drive if available.
    Returns True on success (or skipped if drive is offline), False on error.
    """
    if is_market_hours():
        _log.info("⏸️ Active NYSE market hours in progress (6:30 AM - 1:15 PM PST). Skipping Data sync to prevent resource/file locks.")
        return True # Safe bypass to prevent locking active databases/workbooks during trading
        
    src = BASE_DIR / "Data"
    dst = r"\\10.0.0.156\Storage\Yura\Develop\StockTrading\AnalyzeFinData\Data"
    
    if not src.exists():
        _log.warning(f"⚠️ Source Data folder does not exist: {src}")
        return False
        
    try:
        dst_path = Path(dst)
        z_drive = dst_path.anchor
        if not os.path.exists(z_drive):
            _log.warning(f"⚠️ Backup drive {z_drive} is not connected or accessible. Skipping Data sync.")
            return True # Not a failure of sync itself, just offline
            
        dst_path.mkdir(parents=True, exist_ok=True)
        _log.console(f"🔄 Syncing Data folder to: {dst} ...")
        # Use /E (recursive copy, NO DELETIONS) instead of /MIR to prevent data loss on backup drive
        cmd = ["robocopy", str(src), dst, "/E", "/R:1", "/W:1", "/MT:8", "/NFL", "/NDL", "/NJH", "/NJS"]
        result = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
        if result.returncode < 8:
            _log.info(f"✅ Data folder successfully synchronized to {dst}.")
            return True
        else:
            _log.error(f"❌ Robocopy sync failed (rc={result.returncode}). Stderr: {result.stderr.strip()}")
            return False
    except Exception as e:
        _log.error(f"❌ Failed to sync Data folder to Z: drive: {e}", exc_info=True)
        return False

def is_pid_running(pid: int) -> bool:
    """Return True if a process with the given PID is actively running on Windows."""
    if pid <= 0:
        return False
    try:
        res = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, errors="replace")
        return "No tasks" not in res.stdout and str(pid) in res.stdout
    except Exception:
        return False

def run_watchdog():
    # Enforce a strict cross-process execution singleton to prevent 2 watchdogs from running concurrently
    lock_file = BASE_DIR / "Data" / "watchdog_run.lock"
    if lock_file.exists():
        try:
            with open(lock_file, "r") as f:
                old_pid = int(f.read().strip())
            if is_pid_running(old_pid):
                _log.warning(f"⚠️ Watchdog execution blocked: another instance is already running (PID={old_pid}). Exiting.")
                return
        except Exception as e:
            _log.warning(f"⚠️ Lock file unreadable or corrupt ({e}). Overriding...")

    # Create or update the lock file with our current PID
    try:
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_file, "w") as f:
            f.write(str(os.getpid()))
        # Register automatic cleanup of the lock file upon process termination
        atexit.register(lambda: lock_file.unlink() if lock_file.exists() else None)
    except Exception as e:
        _log.error(f"❌ Failed to write watchdog lock file: {e}")
        return

    _log.console(f"[{datetime.datetime.now()}] Project AETHER Healer starting...")
    
    # 0. E*TRADE Proactive Session Keeper (Prevents Soft Expiry) — RENEW-ONLY.
    #    Anti-ban rule: automated jobs NEVER open a browser. keep_alive() refreshes a
    #    still-valid same-day token via pure HTTP and makes zero brokerage calls once the
    #    token has expired (nightly midnight-ET / weekend gap). A dead token means a HUMAN
    #    must re-auth from a clean context — we alert, we do NOT auto-launch Playwright
    #    (that stale-session replay is exactly what trips Akamai and gets the IP banned).
    try:
        tokens = etrade.keep_alive("production")
    except Exception as e:
        tokens = None
        _log.error(f"  [Healer] E*TRADE keep_alive raised: {e}", exc_info=True)

    if tokens:
        _log.info("  [Healer] E*TRADE production session is active (renew-only keep-alive).")
    else:
        err_msg = ("E*TRADE session is expired/missing. Automated jobs never open a browser "
                   "(anti-ban). Run 'python scripts/diagnostics/test_etrade.py production' from a "
                   "clean context to re-authenticate.")
        _log.error(f"  🛑 [Healer] {err_msg}")
        try:
            notify.send_email(
                subject="🚨 AETHER: E*TRADE session needs MANUAL re-auth",
                body=f"The AETHER Watchdog keep-alive found no valid E*TRADE session.\n\n{err_msg}",
                is_html=False
            )
        except Exception as ne:
            _log.error(f"  ❌ Failed to send watchdog alert email: {ne}")
        # Fail the scheduler run (rc != 0) for visibility. No browser was launched.
        raise RuntimeError(err_msg)

    # 0b. Chaikin Proactive Session Keeper — uses cross-process singleton
    try:
        session = powergauge.ensure_valid_session()
        if session and session.get("jsessionid"):
            _log.info("  [Healer] Chaikin Analytics session is valid.")
        else:
            _log.error("  [Healer] Chaikin Analytics session renewal failed — manual login required.")
    except Exception as e:
        _log.error("Chaikin session keep-alive failed", extra={"error": str(e)}, exc_info=True)

    # 1. Gather Initial System Health Data
    initial_errors = check_logs()
    missing_tasks = check_task_scheduler()
    data_issue = check_data_freshness()
    
    # 1b. Clean up any stray/duplicate AETHER tasks (Pillar 1 Self-Sanitation)
    purge_stray_tasks()
    
    recovery_actions = []
    ai_triggered = False
    ai_status = ""
    ai_console_log = ""
    original_traceback = extract_latest_traceback()
    
    # 2. Heal task scheduler missing tasks
    if missing_tasks:
        heal_tasks(missing_tasks)
        recovery_actions.append(f"Healed missing tasks: {', '.join(missing_tasks)}")

    # 3. Heal resource locks if permission error is logged
    if any("PERMISSION" in str(err).upper() for err in initial_errors):
        kill_ghost_processes()
        recovery_actions.append("Killed ghost processes to resolve resource lock.")

    # 4. Perform Synchronous AI Self-Healing if a traceback is detected
    if original_traceback and any(word in original_traceback.upper() for word in ["TRACEBACK", "ERROR", "EXCEPTION"]):
        # A Python crash was found. Spawn the blocking AI Healer!
        ai_triggered, ai_status, ai_console_log = trigger_ai_self_healing(original_traceback)
        if ai_triggered:
            recovery_actions.append(f"AI Self-Healer successfully executed: {ai_status}")
        else:
            recovery_actions.append(f"AI Self-Healer triggered but failed: {ai_status}")

    # 5. Post-Healing Verification (Empirical Compilation Check)
    # We run the report script directly to see if the codebase now compiles and executes nominal!
    try:
        val_result = subprocess.run(
            [sys.executable, str(BASE_DIR / "ai_portfolio_game.py"), "--report"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=15
        )
        compilation_passed = (val_result.returncode == 0)
        validation_output = val_result.stdout if compilation_passed else val_result.stderr
    except Exception as e:
        compilation_passed = False
        validation_output = f"Validation execution failed: {e}"

    # 6. Re-Audit Logs after the fix
    remaining_errors = check_logs()
    
    # 6.5. Run Backup Sync and Monitor Success/Errors
    sync_success = sync_data_folder()
    
    # Check if there are any active issues left
    issues = []
    if remaining_errors and not ai_triggered: # If we self-healed, the old log errors are still there, so we ignore them for the "issues" list
        issues.append("REMAINING LOG ERRORS:\n" + "\n".join(remaining_errors))
    if data_issue: 
        issues.append(data_issue)
    if not sync_success:
        issues.append("CRITICAL: Network Backup Data Sync Failed!")

    # 7. Construct the Consolidated HTML Recovery Report (The Final Step!)
    # We send an email if a healing action occurred, an AI healer triggered, there are active code errors in the logs, or the backup sync failed.
    if ai_triggered or recovery_actions or (remaining_errors and not ai_triggered) or not sync_success:
        _log.console("Healer cycle complete. Constructing consolidated recovery report...")
        
        # Color badges
        status_color = "#27ae60" if compilation_passed and sync_success else "#c0392b"
        status_text = "NOMINAL (HEALED)" if compilation_passed and sync_success else "MANUAL INTERVENTION REQUIRED"
        
        # Clean console log for email (last 2000 chars to avoid size limits)
        trimmed_console_log = ai_console_log[-2000:] if ai_console_log else "No AI logs available."
        
        html_report = f"""
        <html>
        <body style="font-family: sans-serif; color: #333; max-width: 800px; margin: 0 auto; padding: 20px; line-height: 1.5;">
            <h2 style="color: #2c3e50; border-bottom: 3px solid #3498db; padding-bottom: 10px; margin-bottom: 25px;">🛡️ Project AETHER: Autonomous Health & Recovery Report</h2>
            
            <!-- Overall Status Badge -->
            <div style="background: {status_color}; color: white; padding: 10px 15px; border-radius: 4px; font-weight: bold; margin-bottom: 30px; font-size: 16px; text-align: center;">
                SYSTEM STATUS: {status_text}
            </div>

            <!-- SECTION 1: DETECTED ISSUE -->
            {f'''
            <div style="background: #fdf2f2; border-left: 5px solid #ec5b5b; padding: 15px; margin-bottom: 25px; border-radius: 4px;">
                <h3 style="margin-top: 0; color: #c0392b; font-size: 15px;">🚨 1. DETECTED ISSUE (Crash Traceback):</h3>
                <pre style="background: #1e1e1e; color: #d4d4d4; padding: 12px; border-radius: 4px; font-size: 12px; overflow-x: auto; font-family: monospace;">{original_traceback}</pre>
            </div>
            ''' if original_traceback else ''}

            <!-- SECTION 2: AI DEBUGGING & HEALING ACTIONS -->
            {f'''
            <div style="background: #eef9ff; border-left: 5px solid #3498db; padding: 15px; margin-bottom: 25px; border-radius: 4px;">
                <h3 style="margin-top: 0; color: #2980b9; font-size: 15px;">🧠 2. AI DEBUGGING & HEALING PROCESS:</h3>
                <p style="font-size: 13px; font-weight: bold; color: #555;">Tool Invoked: <span style="font-family: monospace; background: #e0f2f1; padding: 2px 4px;">{HEALER_CMD_TEMPLATE}</span></p>
                <p style="font-size: 13px; font-weight: bold; color: #555;">Healing Status: <span style="color: {status_color};">{ai_status}</span></p>
                <h4 style="margin-bottom: 5px; font-size: 13px; color: #333;">AI Console Output logs:</h4>
                <pre style="background: #2c3e50; color: #ecf0f1; padding: 12px; border-radius: 4px; font-size: 11px; overflow-x: auto; max-height: 250px; font-family: monospace;">{trimmed_console_log}</pre>
            </div>
            ''' if ai_triggered else ''}

            <!-- SECTION 3: COMPILATION & RESULTS VALIDATION -->
            <div style="background: #f9f9f9; border-left: 5px solid #95a5a6; padding: 15px; margin-bottom: 25px; border-radius: 4px;">
                <h3 style="margin-top: 0; color: #34495e; font-size: 15px;">✅ 3. POST-HEALING VALIDATION (Execution Check):</h3>
                <p style="font-size: 13px; font-weight: bold;">Validation Script: <span style="font-family: monospace; background: #ddd; padding: 2px 4px;">python ai_portfolio_game.py --report</span></p>
                <p style="font-size: 13px; font-weight: bold;">Compilation Result: <span style="color: {'#27ae60' if compilation_passed else '#c0392b'}; font-size: 14px;">{'SUCCESS / PASSED' if compilation_passed else 'FAILED / COMPILE ERROR'}</span></p>
                <h4 style="margin-bottom: 5px; font-size: 13px; color: #333;">Validation Console Output:</h4>
                <pre style="background: #f1f2f6; color: #2c3e50; padding: 12px; border-radius: 4px; border: 1px solid #ddd; font-size: 12px; overflow-x: auto; font-family: monospace;">{validation_output}</pre>
            </div>

            <!-- SECTION 3b: DATA BACKUP SYNC STATUS -->
            <div style="background: {'#f9f9f9' if sync_success else '#fdf2f2'}; border-left: 5px solid {'#34495e' if sync_success else '#ec5b5b'}; padding: 15px; margin-bottom: 25px; border-radius: 4px;">
                <h3 style="margin-top: 0; color: {'#34495e' if sync_success else '#c0392b'}; font-size: 15px;">📁 3b. DATA BACKUP SYNC STATUS:</h3>
                <p style="font-size: 13px; font-weight: bold;">Backup Location: <span style="font-family: monospace; background: #ddd; padding: 2px 4px;">\\\\10.0.0.156\\Storage\\Yura\\Develop\\StockTrading\\AnalyzeFinData\\Data</span></p>
                <p style="font-size: 13px; font-weight: bold;">Sync Status: <span style="color: {'#27ae60' if sync_success else '#c0392b'}; font-size: 14px;">{'SUCCESS / NOMINAL' if sync_success else 'FAILED / SYNC ERROR'}</span></p>
            </div>

            <!-- SECTION 4: NEXT STEPS -->
            <div style="background: #fff9db; border-left: 5px solid #f59f00; padding: 15px; margin-bottom: 30px; border-radius: 4px;">
                <h3 style="margin-top: 0; color: #f08c00; font-size: 15px;">🏁 4. RESULTS & NEXT STEPS:</h3>
                <ul style="font-size: 13px; padding-left: 20px; color: #555; line-height: 1.6;">
                    {'<li><b>AETHER Self-Healer:</b> Surgically patched the codebase and pushed the fix to the main branch.</li>' if compilation_passed and ai_triggered else ''}
                    {'<li><b>Automatic Resume:</b> Normal scheduled trading tasks will continue on their next hourly trigger.</li>' if compilation_passed else ''}
                    {'<li><b>Action Required:</b> Please delete the circuit breaker lock file at <span style="font-family: monospace; background: #ffe0b2; padding: 2px 4px;">Data/self_healing.lock</span> to enable future self-healing runs once you are satisfied with this fix.</li>' if ai_triggered else ''}
                    {'<li><b>Alert:</b> The codebase failed to compile after the self-healing attempt. Immediate manual developer intervention is required.</li>' if not compilation_passed else ''}
                    {f'<li><b>Backup Status:</b> Robocopy sync completed successfully.</li>' if sync_success else '<li><b>Backup Status Alert:</b> robocopy was unable to push updates to \\\\10.0.0.156\\Storage\\ - check server connection.</li>'}
                </ul>
            </div>

            <p style="border-top: 1px solid #eee; padding-top: 15px; font-size: 11px; color: #7f8c8d;">
                🛡️ <i>AETHER Watchdog Healer | Autonomic Recovery Systems | Project: AnalyzeFinData</i>
            </p>
        </body>
        </html>
        """
        
        notify.send_email("🛡️ Project AETHER: Autonomous Health & AI Recovery Report", html_report, is_html=True)
        _log.info("Consolidated Recovery Report emailed successfully!")
    else:
        _log.info("✅ System Health Check: All systems nominal.")

if __name__ == "__main__":
    run_watchdog()
