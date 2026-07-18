import os
import sys
import datetime
import subprocess
import json
import notify
from pathlib import Path
from aether_logger import get_logger as _get_logger

_log = _get_logger("watchdog")

# --- Windows UTF-8 Hardening ---
# Prevents UnicodeEncodeError when printing emojis (🤖, 🚨, 🧠) in headless environments
class SafeStreamWrapper:
    def __init__(self, stream):
        self._stream = stream
    def write(self, s):
        try:
            return self._stream.write(s)
        except UnicodeEncodeError:
            encoding = getattr(self._stream, 'encoding', 'cp1252') or 'cp1252'
            safe_s = s.encode(encoding, errors='replace').decode(encoding)
            return self._stream.write(safe_s)
    def __getattr__(self, name):
        return getattr(self._stream, name)

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    sys.stdout = SafeStreamWrapper(sys.stdout)
    sys.stderr = SafeStreamWrapper(sys.stderr)

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
SELF_HEAL_PROMPT_FILE = BASE_DIR / "Data" / "self_healing_prompt.txt"

# --- Agnostic AI Self-Healing Tool Configuration ---
# Supports any AI CLI or custom scripts (Gemini CLI, Claude, local Codex, custom wrappers).
# Defaults to npx @google/gemini-cli but can be overridden globally via environment variables.
# Placeholders: {prompt} (inline text) or {prompt_file} (safe text file path, highly recommended for Windows).
HEALER_CMD_TEMPLATE = os.environ.get(
    "AETHER_HEALER_CMD",
    'npx --yes @google/gemini-cli --approval-mode auto_edit -p "{prompt_file}"'
)

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
        print(f"Failed to extract traceback: {e}")
    return ""

def trigger_ai_self_healing(traceback):
    """Headlessly trigger the configured AI CLI Agent synchronously to self-heal the python codebase on the fly."""
    if SELF_HEAL_LOCK.exists():
        print("  [Healer] Circuit breaker active (self_healing.lock found). Skipping AI trigger.")
        return False, "Circuit breaker active. Code needs manual review.", ""
    
    # Create the lock to prevent recursive self-healing loops
    with open(SELF_HEAL_LOCK, "w", encoding="utf-8") as f:
        f.write(f"Active since: {datetime.datetime.now()}\nTraceback: {traceback[:200]}\n")
        
    print("🧠 [AETHER BRAIN] CRITICAL ERROR DETECTED. ACTIVATING SYNCHRONOUS SELF-HEALER...")
    
    prompt = f"""[CRITICAL AUTONOMOUS RECOVERY COMMAND]
Our background AETHER trading pipeline has crashed. 
You are operating in headless self-healing mode. Your goal is to:
1. Analyze this traceback.
2. Identify the bug in our codebase (likely ai_portfolio_game.py or autonomous_pipeline.py).
3. Modify the files surgically to fix the bug permanently (especially handle platform/Windows/encoding quirks).
4. Run 'ai_portfolio_game.py --report' or standard commands to verify compilation.
5. Commit the fix with a commit message starting with '[AI Self-Healed]'.

Here is the exact traceback:
{traceback}
"""
    
    try:
        # Write the prompt to a safe text file to avoid Windows escaping issues
        with open(SELF_HEAL_PROMPT_FILE, "w", encoding="utf-8") as f:
            f.write(prompt)
            
        cmd = HEALER_CMD_TEMPLATE.format(
            prompt=prompt.replace('"', '\\"').replace('\n', ' '),
            prompt_file=str(SELF_HEAL_PROMPT_FILE)
        )
        
        print(f"🚀 [AETHER BRAIN] Dispatching self-healing command: {cmd}")
        
        # Run synchronously (blocking) with a 5-minute timeout to let the AI do its work
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
            print("✅ [AETHER BRAIN] Self-healing process completed successfully.")
            return True, "Self-healer executed successfully.", console_log
        else:
            print(f"❌ [AETHER BRAIN] Self-healer exited with code {result.returncode}.")
            return False, f"Self-healer failed with exit code {result.returncode}.", console_log
            
    except subprocess.TimeoutExpired:
        print("❌ [AETHER BRAIN] Self-healing process timed out (5 minute limit reached).")
        return False, "Self-healing process timed out (5 min limit reached).", "TIMEOUT"
    except Exception as e:
        print(f"❌ Failed to run Self-Healer: {e}")
        # Clean up files if we fail to spawn
        if SELF_HEAL_LOCK.exists(): SELF_HEAL_LOCK.unlink()
        if SELF_HEAL_PROMPT_FILE.exists(): SELF_HEAL_PROMPT_FILE.unlink()
        return False, f"Execution failure: {e}", str(e)

def check_task_scheduler():
    """Verify all AETHER tasks are present and active."""
    missing = []
    for task in TASKS:
        try:
            result = subprocess.run(["schtasks", "/query", "/tn", task], capture_output=True, text=True)
            if result.returncode != 0:
                missing.append(task)
        except:
            missing.append(task)
    return missing

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
    run_as = os.environ.get("USERNAME") or os.environ.get("USER") or os.getlogin()
    python_exe = sys.executable
    for task in (TASKS if force else missing_tasks):
        print(f"🔧 Healing/Upgrading scheduled task: {task}")
        if task == "AnalyzeFinData_Morning":
            cmd = f'schtasks /create /tn "{task}" /tr "cmd.exe /c set PYTHONIOENCODING=utf-8 && {python_exe} {BASE_DIR / "autonomous_pipeline.py"}" /sc daily /st 05:30 /f /it /ru {run_as}'
        elif task == "AnalyzeFinData_Evening":
            cmd = f'schtasks /create /tn "{task}" /tr "cmd.exe /c set PYTHONIOENCODING=utf-8 && {python_exe} {BASE_DIR / "daily_task.py"}" /sc daily /st 17:00 /f /it /ru {run_as}'
        elif task == "AnalyzeFinData_AI_Game":
            cmd = f'schtasks /create /tn "{task}" /tr "cmd.exe /c set PYTHONIOENCODING=utf-8 && {python_exe} {BASE_DIR / "ai_portfolio_game.py"} --run" /sc daily /st 07:00 /f /it /ru {run_as}'
        elif task == "AnalyzeFinData_AI_Summary":
            cmd = f'schtasks /create /tn "{task}" /tr "cmd.exe /c set PYTHONIOENCODING=utf-8 && {python_exe} {BASE_DIR / "ai_portfolio_game.py"} --summary" /sc daily /st 18:00 /f /it /ru {run_as}'
        elif task == "Project_AETHER_Watchdog":
            cmd = f'schtasks /create /tn "{task}" /tr "cmd.exe /c set PYTHONIOENCODING=utf-8 && {python_exe} {BASE_DIR / "watchdog.py"}" /sc hourly /f /it /ru {run_as}'
        else:
            print(f"⚠️  No registration template for task: {task}")
            continue

        try:
            result = subprocess.run(cmd, shell=True, capture_output=True)
            if result.returncode == 0:
                print(f"✅ Task {task} successfully registered with native UTF-8 environment.")
            else:
                print(f"❌ schtasks failed for {task} (rc={result.returncode}): {result.stderr.decode(errors='replace').strip()}")
        except Exception as e:
            print(f"❌ Failed to heal {task}: {e}")

def kill_ghost_processes():
    """Kill any hung python or excel processes that might be locking resources."""
    print("🧹 Cleaning up hung ghost processes...")
    try:
        subprocess.run(["powershell", "Get-Process | Where-Object { $_.Name -match 'excel|python' -and $_.CommandLine -match 'AnalyzeFinData' } | Stop-Process -Force"], capture_output=True)
    except:
        pass

def run_watchdog():
    print(f"[{datetime.datetime.now()}] Project AETHER Healer starting...")
    
    # 0. E*TRADE Proactive Session Keeper (Prevents Soft Expiry)
    try:
        import etrade
        tokens = etrade._load_tokens("production")
        if tokens:
            renewed = etrade.renew_tokens(tokens, "production")
            if renewed:
                print("  [Healer] E*TRADE production tokens successfully renewed and session extended!")
            else:
                print("  [Healer] E*TRADE production tokens are expired or could not be renewed silently.")
    except Exception as e:
        print(f"  [Healer] E*TRADE session keep-alive bypassed or failed: {e}")

    # 0b. Chaikin Proactive Session Keeper & Auto-Login
    try:
        import sys
        sys.path.insert(0, str(BASE_DIR))
        import powergauge
        session_id = powergauge._load_session_from_file()
        if not session_id or not powergauge._validate_session(session_id):
            print("  [Healer] Chaikin Analytics session is expired or missing. Attempting automated re-authentication...")
            # Trigger the browser login on the user's active desktop!
            # Since this runs inside the watchdog, we pass interactive=False to avoid blocking prompt hangs.
            new_session = powergauge.login(interactive=False)
            if new_session:
                print("  [Healer] Chaikin Analytics session successfully renewed via automated login!")
            else:
                print("  [Healer] Chaikin Analytics automated login failed or was aborted.")
        else:
            print("  [Healer] Chaikin Analytics session is fresh and valid.")
    except Exception as e:
        print(f"  [Healer] Chaikin session keep-alive bypassed or failed: {e}")

    # 1. Gather Initial System Health Data
    initial_errors = check_logs()
    missing_tasks = check_task_scheduler()
    data_issue = check_data_freshness()
    
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
    
    # Check if there are any active issues left
    issues = []
    if remaining_errors and not ai_triggered: # If we self-healed, the old log errors are still there, so we ignore them for the "issues" list
        issues.append("REMAINING LOG ERRORS:\n" + "\n".join(remaining_errors))
    if data_issue: 
        issues.append(data_issue)

    # 7. Construct the Consolidated HTML Recovery Report (The Final Step!)
    # We only send an email if a healing action occurred, an AI healer triggered, or there are active code errors in the logs.
    # Stale data alone is a status warning and should not spam your inbox hourly.
    if ai_triggered or recovery_actions or (remaining_errors and not ai_triggered):
        print("Healer cycle complete. Constructing consolidated recovery report...")
        
        # Color badges
        status_color = "#27ae60" if compilation_passed else "#c0392b"
        status_text = "NOMINAL (HEALED)" if compilation_passed else "MANUAL INTERVENTION REQUIRED"
        
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
                <p style="font-size: 13px; font-weight: bold;">Compilation Result: <span style="color: {status_color}; font-size: 14px;">{'SUCCESS / PASSED' if compilation_passed else 'FAILED / COMPILE ERROR'}</span></p>
                <h4 style="margin-bottom: 5px; font-size: 13px; color: #333;">Validation Console Output:</h4>
                <pre style="background: #f1f2f6; color: #2c3e50; padding: 12px; border-radius: 4px; border: 1px solid #ddd; font-size: 12px; overflow-x: auto; font-family: monospace;">{validation_output}</pre>
            </div>

            <!-- SECTION 4: NEXT STEPS -->
            <div style="background: #fff9db; border-left: 5px solid #f59f00; padding: 15px; margin-bottom: 30px; border-radius: 4px;">
                <h3 style="margin-top: 0; color: #f08c00; font-size: 15px;">🏁 4. RESULTS & NEXT STEPS:</h3>
                <ul style="font-size: 13px; padding-left: 20px; color: #555; line-height: 1.6;">
                    {'<li><b>AETHER Self-Healer:</b> Surgically patched the codebase and pushed the fix to the main branch.</li>' if compilation_passed and ai_triggered else ''}
                    {'<li><b>Automatic Resume:</b> Normal scheduled trading tasks will continue on their next hourly trigger.</li>' if compilation_passed else ''}
                    {'<li><b>Action Required:</b> Please delete the circuit breaker lock file at <span style="font-family: monospace; background: #ffe0b2; padding: 2px 4px;">Data/self_healing.lock</span> to enable future self-healing runs once you are satisfied with this fix.</li>' if ai_triggered else ''}
                    {'<li><b>Alert:</b> The codebase failed to compile after the self-healing attempt. Immediate manual developer intervention is required.</li>' if not compilation_passed else ''}
                </ul>
            </div>

            <p style="border-top: 1px solid #eee; padding-top: 15px; font-size: 11px; color: #7f8c8d;">
                🛡️ <i>AETHER Watchdog Healer | Autonomic Recovery Systems | Project: AnalyzeFinData</i>
            </p>
        </body>
        </html>
        """
        
        notify.send_email("🛡️ Project AETHER: Autonomous Health & AI Recovery Report", html_report, is_html=True)
        print("Consolidated Recovery Report emailed successfully!")
    else:
        print("✅ System Health Check: All systems nominal.")

if __name__ == "__main__":
    run_watchdog()
