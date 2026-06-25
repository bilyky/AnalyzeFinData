import os
import sys
import datetime
import subprocess
import notify
import json
from pathlib import Path

# --- CONFIGURATION ---
BASE_DIR = Path(__file__).resolve().parent
LOG_FILES = [
    BASE_DIR / "Data" / "autonomous_run.log",
    BASE_DIR / "daily_task.log"
]
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
    'npx @google/gemini-cli --approval-mode auto_edit -p "{prompt_file}"'
)

def check_logs():
    """Audit logs for 'Error', 'Failed', or 'Fatal' entries in the last 24h."""
    errors = []
    now = datetime.datetime.now()
    for log_path in LOG_FILES:
        if not log_path.exists():
            continue
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
            for line in lines[-50:]: # Check last 50 lines
                if any(word in line.upper() for word in ["ERROR", "FAILED", "FATAL", "CRASH", "TRACEBACK", "UNBOUNDLOCALERROR", "UNICODEENCODEERROR"]):
                    try:
                        log_date_str = line.split("]")[0].strip("[")
                        log_date = datetime.datetime.strptime(log_date_str, "%Y-%m-%d %H:%M:%S")
                        if (now - log_date).total_seconds() < 86400:
                            errors.append(f"[{log_path.name}] {line.strip()}")
                    except:
                        errors.append(f"[{log_path.name}] {line.strip()}")
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
    """Headlessly trigger any configured AI CLI Agent to self-heal the python codebase on the fly."""
    if SELF_HEAL_LOCK.exists():
        print("  [Healer] Circuit breaker active (self_healing.lock found). Skipping AI trigger to prevent loops.")
        return False, "Circuit breaker active. Code needs manual review."
    
    # Create the lock to prevent recursive self-healing loops
    with open(SELF_HEAL_LOCK, "w", encoding="utf-8") as f:
        f.write(f"Active since: {datetime.datetime.now()}\nTraceback: {traceback[:200]}\n")
        
    print("🧠 [AETHER BRAIN] CRITICAL ERROR DETECTED. ACTIVATING AUTONOMOUS SELF-HEALER...")
    
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
        # Write the prompt to a safe text file to avoid any terminal command-line escaping bugs on Windows
        with open(SELF_HEAL_PROMPT_FILE, "w", encoding="utf-8") as f:
            f.write(prompt)
            
        # Dynamically build the command from the template
        # Support both inline {prompt} and the highly secure {prompt_file}
        cmd = HEALER_CMD_TEMPLATE.format(
            prompt=prompt.replace('"', '\\"').replace('\n', ' '),
            prompt_file=str(SELF_HEAL_PROMPT_FILE)
        )
        
        print(f"🚀 [AETHER BRAIN] Dispatching self-healing command: {cmd}")
        
        # Run in background to let the watchdog finish, but allow it to run autonomously
        subprocess.Popen(cmd, shell=True, cwd=str(BASE_DIR))
        return True, "AETHER Self-Healing session spawned in the background."
    except Exception as e:
        print(f"❌ Failed to spawn Self-Healer: {e}")
        # Clean up files if we fail to spawn
        if SELF_HEAL_LOCK.exists(): SELF_HEAL_LOCK.unlink()
        if SELF_HEAL_PROMPT_FILE.exists(): SELF_HEAL_PROMPT_FILE.unlink()
        return False, f"Spawn failure: {e}"

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

def heal_tasks(missing_tasks):
    """Attempt to re-register tasks that have disappeared or failed."""
    for task in missing_tasks:
        print(f"🔧 Attempting to heal task: {task}")
        if task == "AnalyzeFinData_Morning":
            cmd = f'schtasks /create /tn "{task}" /tr "{sys.executable} {BASE_DIR / "autonomous_pipeline.py"}" /sc daily /st 05:30 /f /it /ru yufa'
        elif task == "AnalyzeFinData_Evening":
            cmd = f'schtasks /create /tn "{task}" /tr "{sys.executable} {BASE_DIR / "daily_task.py"}" /sc daily /st 17:00 /f /it /ru yufa'
        elif task == "Project_AETHER_Watchdog":
            cmd = f'schtasks /create /tn "{task}" /tr "{sys.executable} {BASE_DIR / "watchdog.py"}" /sc hourly /f /it /ru yufa'
        else:
            continue
        
        try:
            subprocess.run(cmd, shell=True, capture_output=True)
            print(f"✅ Task {task} re-registered.")
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
    
    log_errors = check_logs()
    missing_tasks = check_task_scheduler()
    data_issue = check_data_freshness()
    
    recovery_actions = []
    
    # 1. If tasks are missing, heal them
    if missing_tasks:
        heal_tasks(missing_tasks)
        recovery_actions.append(f"Healed missing tasks: {', '.join(missing_tasks)}")

    # 2. If log errors suggest a lock, kill ghost processes
    if any("PERMISSION" in str(err).upper() for err in log_errors):
        kill_ghost_processes()
        recovery_actions.append("Killed ghost processes to resolve resource lock.")

    # 3. Autonomous AI Self-Healing Trigger
    ai_triggered = False
    ai_status = ""
    tb = extract_latest_traceback()
    if tb and any(word in tb.upper() for word in ["TRACEBACK", "ERROR", "EXCEPTION"]):
        # A Python crash traceback was detected in the logs!
        ai_triggered, ai_status = trigger_ai_self_healing(tb)
        if ai_triggered:
            recovery_actions.append(f"AI Self-Healer deployed: {ai_status}")

    # 4. Final Audit
    remaining_errors = check_logs()
    
    issues = []
    if remaining_errors: issues.append("REMAINING LOG ERRORS:\n" + "\n".join(remaining_errors))
    if data_issue: issues.append(data_issue)
    
    if recovery_actions or issues:
        report = []
        if recovery_actions: report.append("🛡️ AUTONOMOUS RECOVERY ACTIONS:\n" + "\n".join(recovery_actions))
        if issues: report.append("🚨 REMAINING ISSUES:\n" + "\n".join(issues))
        
        alert_msg = "\n\n".join(report)
        print("Healer finished with actions or issues. Sending report...")
        notify.send_email("🛡️ Project AETHER: Health & Recovery Report", alert_msg)
    else:
        print("✅ System Health Check: All systems nominal.")

if __name__ == "__main__":
    run_watchdog()
