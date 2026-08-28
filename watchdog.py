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
import threading
from aether import etrade
import powergauge
from pathlib import Path
from aether import trash
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
TASKS = ["AnalyzeFinData_Morning", "AnalyzeFinData_AI_Game", "AnalyzeFinData_AI_Summary", "AnalyzeFinData_Evening", "AnalyzeFinData_ETrade_Reauth"]
SELF_HEAL_LOCK = BASE_DIR / "Data" / "self_healing.lock"

python_exe = sys.executable
_TASK_DEFS = {
    "AnalyzeFinData_Morning":  (f"'{python_exe}' '{BASE_DIR / 'autonomous_pipeline.py'}'", "daily", "05:30"),
    "AnalyzeFinData_Evening":  (f"'{python_exe}' '{BASE_DIR / 'daily_task.py'}'",           "daily", "17:00"),
    "AnalyzeFinData_AI_Game":  (f"'{python_exe}' '{BASE_DIR / 'ai_portfolio_game.py'}' --run", "daily", "07:00"),
    "AnalyzeFinData_AI_Summary": (f"'{python_exe}' '{BASE_DIR / 'ai_portfolio_game.py'}' --summary", "daily", "18:00"),
    # Unattended daily E*TRADE re-auth at 05:15 — just before the 05:30 Morning pipeline so the
    # token is fresh for it. Renew-first + trusted-profile-only + once/day: ≤1 browser/day, and
    # none at all once trust lapses (it latches sms_required and emails/pushes for a human).
    "AnalyzeFinData_ETrade_Reauth": (f"'{python_exe}' '{BASE_DIR / 'server.py'}' etrade-reauth --scheduled", "daily", "05:15"),
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
        # Clean up files if we fail to spawn (ephemeral locks — hard delete)
        trash.soft_delete(SELF_HEAL_LOCK, reason="self-heal-lock", force=True)
        trash.soft_delete(SELF_HEAL_PROMPT_FILE, reason="self-heal-prompt", force=True)
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

def check_port_8888_sentry():
    """Verify if multiple processes are trying to bind to port 8888 (the Web UI port)
    and automatically terminate any duplicate server.py processes.
    """
    if sys.platform != "win32":
        return
    try:
        # Run netstat to find listening processes on port 8888
        res = subprocess.run(["cmd.exe", "/c", "netstat -ano | findstr :8888"], capture_output=True, text=True, errors="ignore")
        pids = set()
        for line in res.stdout.splitlines():
            if "listening" in line.lower():
                parts = line.strip().split()
                if parts:
                    pid = parts[-1]
                    if pid.isdigit() and int(pid) > 0:
                        pids.add(int(pid))
        
        if len(pids) > 1:
            _log.warning(f"⚠️ Port Sentry: Multiple processes ({pids}) binding to port 8888!")
            server_processes = []
            for pid in pids:
                cmd = ["powershell.exe", "-NoProfile", "-Command", f"Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}' | Select-Object CreationDate, CommandLine | ConvertTo-Json"]
                proc_res = subprocess.run(cmd, capture_output=True, text=True, errors="ignore")
                if proc_res.returncode == 0 and proc_res.stdout.strip():
                    try:
                        proc_data = json.loads(proc_res.stdout)
                        cmdline = proc_data.get("CommandLine") or ""
                        if "server.py" in cmdline.lower():
                            creation_date = proc_data.get("CreationDate") or ""
                            server_processes.append((pid, creation_date))
                    except Exception:
                        pass
            
            if len(server_processes) > 1:
                # Sort by creation date ascending (oldest first). Keep the newest (last index) and terminate the rest.
                server_processes.sort(key=lambda x: x[1])
                to_terminate = server_processes[:-1]
                for pid, cdate in to_terminate:
                    _log.warning(f"🧹 Port Sentry: Terminating duplicate older server.py process (PID={pid}, Created={cdate})")
                    subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
    except Exception as e:
        _log.warning(f"Port Sentry check failed (non-fatal): {e}")


def clean_orphaned_containers():
    """Identify and force-terminate orphaned powershell.exe and conhost.exe processes
    older than 30 minutes with zero active children.
    """
    if sys.platform != "win32":
        return
    try:
        # Run PowerShell to find and kill orphaned conhost.exe and powershell.exe
        ps_cmd = (
            "$now = Get-Date; "
            "Get-CimInstance Win32_Process -Filter \"Name = 'powershell.exe' or Name = 'conhost.exe'\" | "
            "ForEach-Object { "
            "  $pid = $_.ProcessId; "
            "  $children = Get-CimInstance Win32_Process -Filter \"ParentProcessId = $pid\"; "
            "  $child_count = if ($children) { @($children).Count } else { 0 }; "
            "  if ($child_count -eq 0) { "
            "    $age_min = ($now - $_.CreationDate).TotalMinutes; "
            "    if ($age_min -gt 30) { "
            "      Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue; "
            "      Write-Output \"Purged orphaned process PID=$pid (Age: [int]$age_min min)\"; "
            "    } "
            "  } "
            "}"
        )
        cmd = ["powershell.exe", "-NoProfile", "-Command", ps_cmd]
        res = subprocess.run(cmd, capture_output=True, text=True, errors="ignore")
        for line in res.stdout.splitlines():
            if line.strip():
                _log.info(f"🧹 Process Sentry: {line.strip()}")
    except Exception as e:
        _log.warning(f"Orphaned process cleanup failed (non-fatal): {e}")


def supervise_processes():
    """R&D #29: Autonomous Process Supervisor & Port Sentry.
    Detects and cleans up port 8888 socket duplicates and orphaned console host processes.
    """
    _log.console("🔍 Running Autonomous Process Supervisor & Port Sentry (R&D #29)...")
    check_port_8888_sentry()
    clean_orphaned_containers()


def purge_stray_tasks():
    """Detect Task Scheduler entries in our namespace that are not in the TASKS white-list.

    Log-only by default: deletion is DESTRUCTIVE and can catch legitimate ad-hoc tasks
    (e.g. a *_Test entry), so a stray is only auto-deleted when AETHER_PURGE_STRAY_TASKS=1
    is explicitly set. Otherwise it is reported and left in place.
    """
    do_delete = os.environ.get("AETHER_PURGE_STRAY_TASKS", "").strip() == "1"
    try:
        # Query all scheduled tasks recursively in LIST format with verbose details
        result = subprocess.run(["schtasks", "/query", "/fo", "LIST", "/v"], capture_output=True, text=True, errors="replace")
        if result.returncode == 0:
            tasks = []
            current = {}
            for line in result.stdout.splitlines():
                if not line.strip():
                    if current and "taskname" in current:
                        tasks.append(current)
                        current = {}
                    continue
                if ":" in line:
                    parts = line.split(":", 1)
                    k = parts[0].strip().lower()
                    v = parts[1].strip()
                    current[k] = v
            if current and "taskname" in current:
                tasks.append(current)

            # Filter for AETHER / AnalyzeFinData namespace
            aether_tasks = [t["taskname"] for t in tasks if "aether" in t["taskname"].lower() or "analyzefindata" in t["taskname"].lower()]

            # Whitelisted production tasks
            whitelist = set(TASKS + ["Project_AETHER_Watchdog"])

            # Attempt to parse sub_tasks dynamically from scripts/utils/register_agent_tasks.ps1 (R&D #4)
            sub_tasks = ["AETHER_DailyDriver", "AETHER_PostMarketReporter", "AETHER_PostMarketSync", "AETHER_PreFlight_Audit", "AETHER_RD_Scientist", "AETHER_StopMonitor", "AETHER_Watchdog"]
            ps1_path = BASE_DIR / "scripts" / "utils" / "register_agent_tasks.ps1"
            if ps1_path.exists():
                try:
                    with open(ps1_path, "r", encoding="utf-8", errors="ignore") as f:
                        ps1_content = f.read()
                    discovered = re.findall(r'Name\s*=\s*\"([^\"]+)\"', ps1_content)
                    if discovered:
                        # Unify discovered subtasks with our standard fallback list
                        sub_tasks = list(set(sub_tasks + discovered))
                except Exception as pe:
                    _log.warning(f"Failed to dynamically parse register_agent_tasks.ps1 (using fallback): {pe}")

            for st in sub_tasks:
                whitelist.add(f"\\AETHER_Agents\\{st}")
                whitelist.add(st) # also direct name

            stray_tasks = []
            for name in aether_tasks:
                # Get the bare name (without folder)
                bare_name = name.split("\\")[-1]
                if name not in whitelist and bare_name not in whitelist:
                    stray_tasks.append(name)
            
            if stray_tasks:
                if not do_delete:
                    _log.warning(f"[Stray-Task] Detected {len(stray_tasks)} task(s) outside the production white-list "
                                 f"(log-only; set AETHER_PURGE_STRAY_TASKS=1 to delete): {stray_tasks}")
                else:
                    _log.warning(f"[Stray-Task] Purging {len(stray_tasks)} stray task(s) (AETHER_PURGE_STRAY_TASKS=1): {stray_tasks}")
                    for t in stray_tasks:
                        del_res = subprocess.run(["schtasks", "/delete", "/tn", t, "/f"], capture_output=True, text=True, errors="replace")
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
        # Use /E (recursive copy, NO DELETIONS) instead of /MIR to prevent data loss on backup drive.
        # Exclude the massive 'Symbol' cache subdirectory (500k+ files) via /XD to avoid network hangs.
        cmd = ["robocopy", str(src), dst, "/E", "/XD", "Symbol", "/R:1", "/W:1", "/MT:8", "/NFL", "/NDL", "/NJH", "/NJS"]
        result = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=120)
        if result.returncode < 8:
            _log.info(f"✅ Data folder successfully synchronized to {dst}.")
            return True
        else:
            _log.error(f"❌ Robocopy sync failed (rc={result.returncode}). Stderr: {result.stderr.strip()}")
            return False
    except subprocess.TimeoutExpired:
        _log.warning("⚠️ Data folder sync timed out (120s limit reached). Skipping sync to prevent hanging.")
        return True # Treat timeout as a safe skip, not a fatal crash of the watchdog
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

def is_blocked_by_active_lock() -> bool:
    """Check for active or stale watchdog locks to enforce a single execution instance."""
    lock_file = BASE_DIR / "Data" / "watchdog_run.lock"
    if lock_file.exists():
        try:
            with open(lock_file, "r") as f:
                old_pid = int(f.read().strip())
            if is_pid_running(old_pid):
                _log.warning(f"⚠️ Watchdog execution blocked: another instance is already running (PID={old_pid}). Exiting.")
                return True
        except Exception as e:
            _log.warning(f"⚠️ Lock file unreadable or corrupt ({e}). Overriding...")

    try:
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_file, "w") as f:
            f.write(str(os.getpid()))
        atexit.register(lambda: trash.soft_delete(lock_file, reason="watchdog-lock", force=True))
    except Exception as e:
        _log.error(f"❌ Failed to write watchdog lock file: {e}")
        return True
    return False


def maintain_etrade_session():
    """Execute E*TRADE proactive session maintenance and re-authentication loops."""
    try:
        rr = etrade.scheduled_reauth("production")
    except Exception as e:
        rr = {"ok": False, "reason": "error"}
        _log.error(f"  [Healer] E*TRADE scheduled_reauth raised: {e}", exc_info=True)

    if rr.get("ok"):
        _log.info(f"  [Healer] E*TRADE production session OK ({rr.get('reason')}).")
    elif rr.get("reason") in ("sms_required", "unseeded", "failed"):
        _log.error(f"  🛑 [Healer] E*TRADE needs manual re-auth (reason: {rr.get('reason')}).")
        try:
            notify.send_reauth_alert("production", rr["reason"])
        except Exception as ne:
            _log.error(f"  ❌ Failed to send E*TRADE re-auth alert: {ne}")
    else:
        _log.info(f"  [Healer] E*TRADE re-auth deferred (reason: {rr.get('reason')}). No action.")


def maintain_chaikin_session():
    """Execute Chaikin proactive session maintenance and keep-alive."""
    try:
        session = powergauge.ensure_valid_session()
        if session and session.get("jsessionid"):
            _log.info("  [Healer] Chaikin Analytics session is valid.")
        else:
            _log.error("  [Healer] Chaikin Analytics session renewal failed — manual login required.")
    except Exception as e:
        _log.error("Chaikin session keep-alive failed", extra={"error": str(e)}, exc_info=True)


def dispatch_async_backup_sync():
    """Spawn the data folder backup sync in a separate thread to prevent network latency from blocking."""
    _log.info("🚀 Dispatched backup data sync to background thread...")
    t = threading.Thread(target=sync_data_folder, daemon=True)
    t.start()


def run_watchdog():
    # Enforce strict cross-process single instance execution
    if is_blocked_by_active_lock():
        return

    _log.console(f"[{datetime.datetime.now()}] Project AETHER Healer starting...")

    # Step 0: Maintain broker sessions
    maintain_etrade_session()
    maintain_chaikin_session()

    # Step 1: Gather health statistics
    initial_errors = check_logs()
    missing_tasks = check_task_scheduler()
    data_issue = check_data_freshness()
    
    # Step 2: Clean stray tasks & supervise system processes
    purge_stray_tasks()
    supervise_processes()

    # Step 3: Purge auth-state garbage
    try:
        n_trashed = trash.purge_trash()
        if n_trashed:
            _log.info(f"  [Healer] Purged {n_trashed} expired file(s) from the auth trash.")
    except Exception as e:
        _log.error(f"  [Healer] Trash purge failed: {e}")

    recovery_actions = []
    ai_triggered = False
    ai_status = ""
    ai_console_log = ""
    original_traceback = extract_latest_traceback()
    
    # Step 4: Heal task scheduler duplicates
    if missing_tasks:
        heal_tasks(missing_tasks)
        recovery_actions.append(f"Healed missing tasks: {', '.join(missing_tasks)}")

    # Step 5: Heal resource locks if permissions fail
    if any("PERMISSION" in str(err).upper() for err in initial_errors):
        kill_ghost_processes()
        recovery_actions.append("Killed ghost processes to resolve resource lock.")

    # Step 6: Synchronous AI Self-Healing
    if original_traceback and any(word in original_traceback.upper() for word in ["TRACEBACK", "ERROR", "EXCEPTION"]):
        ai_triggered, ai_status, ai_console_log = trigger_ai_self_healing(original_traceback)
        if ai_triggered:
            recovery_actions.append(f"AI Self-Healer successfully executed: {ai_status}")
        else:
            recovery_actions.append(f"AI Self-Healer triggered but failed: {ai_status}")

    # Step 7: Post-healing validation & execution check
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

    # Step 8: Re-audit logs after healing
    remaining_errors = check_logs()
    
    # Step 9: Non-blocking background backup sync (Asynchronous threading)
    dispatch_async_backup_sync()
    
    # Step 10: Compile and send HTML briefing report if healing or failures occurred
    issues = []
    if remaining_errors and not ai_triggered:
        issues.append("REMAINING LOG ERRORS:\n" + "\n".join(remaining_errors))
    if data_issue: 
        issues.append(data_issue)

    if ai_triggered or recovery_actions or (remaining_errors and not ai_triggered):
        _log.console("Healer cycle complete. Constructing consolidated recovery report...")
        
        status_color = "#27ae60" if compilation_passed else "#c0392b"
        status_text = "NOMINAL (HEALED)" if compilation_passed else "MANUAL INTERVENTION REQUIRED"
        
        trimmed_console_log = ai_console_log[-2000:] if ai_console_log else "No AI logs available."
        
        html_report = f"""
        <html>
        <body style="font-family: sans-serif; color: #333; max-width: 800px; margin: 0 auto; padding: 20px; line-height: 1.5;">
            <h2 style="color: #2c3e50; border-bottom: 3px solid #3498db; padding-bottom: 10px; margin-bottom: 25px;">🛡️ Project AETHER: Autonomous Health & Recovery Report</h2>
            
            <div style="background: {status_color}; color: white; padding: 10px 15px; border-radius: 4px; font-weight: bold; margin-bottom: 30px; font-size: 16px; text-align: center;">
                SYSTEM STATUS: {status_text}
            </div>

            {{f'''
            <div style="background: #fdf2f2; border-left: 5px solid #ec5b5b; padding: 15px; margin-bottom: 25px; border-radius: 4px;">
                <h3 style="margin-top: 0; color: #c0392b; font-size: 15px;">🚨 1. DETECTED ISSUE (Crash Traceback):</h3>
                <pre style="background: #1e1e1e; color: #d4d4d4; padding: 12px; border-radius: 4px; font-size: 12px; overflow-x: auto; font-family: monospace;">{{original_traceback}}</pre>
            </div>
            ''' if original_traceback else ''}}

            {{f'''
            <div style="background: #eef9ff; border-left: 5px solid #3498db; padding: 15px; margin-bottom: 25px; border-radius: 4px;">
                <h3 style="margin-top: 0; color: #2980b9; font-size: 15px;">🧠 2. AI DEBUGGING & HEALING PROCESS:</h3>
                <p style="font-size: 13px; font-weight: bold; color: #555;">Tool Invoked: <span style="font-family: monospace; background: #e0f2f1; padding: 2px 4px;">{{HEALER_CMD_TEMPLATE}}</span></p>
                <p style="font-size: 13px; font-weight: bold; color: #555;">Healing Status: <span style="color: {status_color};">{{ai_status}}</span></p>
                <h4 style="margin-bottom: 5px; font-size: 13px; color: #333;">AI Console Output logs:</h4>
                <pre style="background: #2c3e50; color: #ecf0f1; padding: 12px; border-radius: 4px; font-size: 11px; overflow-x: auto; max-height: 250px; font-family: monospace;">{{trimmed_console_log}}</pre>
            </div>
            ''' if ai_triggered else ''}}

            <div style="background: #f9f9f9; border-left: 5px solid #95a5a6; padding: 15px; margin-bottom: 25px; border-radius: 4px;">
                <h3 style="margin-top: 0; color: #34495e; font-size: 15px;">✅ 3. POST-HEALING VALIDATION (Execution Check):</h3>
                <p style="font-size: 13px; font-weight: bold;">Validation Script: <span style="font-family: monospace; background: #ddd; padding: 2px 4px;">python ai_portfolio_game.py --report</span></p>
                <p style="font-size: 13px; font-weight: bold;">Compilation Result: <span style="color: {{'#27ae60' if compilation_passed else '#c0392b'}}; font-size: 14px;">{{'SUCCESS / PASSED' if compilation_passed else 'FAILED / COMPILE ERROR'}}</span></p>
                <h4 style="margin-bottom: 5px; font-size: 13px; color: #333;">Validation Console Output:</h4>
                <pre style="background: #f1f2f6; color: #2c3e50; padding: 12px; border-radius: 4px; border: 1px solid #ddd; font-size: 12px; overflow-x: auto; font-family: monospace;">{{validation_output}}</pre>
            </div>

            <div style="background: #fff9db; border-left: 5px solid #f59f00; padding: 15px; margin-bottom: 30px; border-radius: 4px;">
                <h3 style="margin-top: 0; color: #f08c00; font-size: 15px;">🏁 4. RESULTS & NEXT STEPS:</h3>
                <ul style="font-size: 13px; padding-left: 20px; color: #555; line-height: 1.6;">
                    {{'<li><b>AETHER Self-Healer:</b> Surgically patched the codebase and pushed the fix to the main branch.</li>' if compilation_passed and ai_triggered else ''}}
                    {{'<li><b>Automatic Resume:</b> Normal scheduled trading tasks will continue on their next hourly trigger.</li>' if compilation_passed else ''}}
                    {{'<li><b>Action Required:</b> Please delete the circuit breaker lock file at <span style="font-family: monospace; background: #ffe0b2; padding: 2px 4px;">Data/self_healing.lock</span> to enable future self-healing runs once you are satisfied with this fix.</li>' if ai_triggered else ''}}
                    {{'<li><b>Alert:</b> The codebase failed to compile after the self-healing attempt. Immediate manual developer intervention is required.</li>' if not compilation_passed else ''}}
                </ul>
            </div>

            <p style="border-top: 1px solid #eee; padding-top: 15px; font-size: 11px; color: #7f8c8d;">
                🛡️ <i>AETHER Watchdog Healer | Autonomic Recovery Systems | Project: AnalyzeFinData</i>
            </p>
        </body>
        </html>
        """
        
        try:
            notify.send_email("🛡️ Project AETHER: Autonomous Health & AI Recovery Report", html_report, is_html=True)
            _log.info("Consolidated Recovery Report emailed successfully!")
        except Exception as e:
            _log.error(f"  [Healer] Failed to dispatch consolidated HTML recovery report: {e}")
    else:
        _log.info("✅ System Health Check: All systems nominal.")


if __name__ == "__main__":
    run_watchdog()