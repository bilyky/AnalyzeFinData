import os
import datetime
import subprocess
import notify
from pathlib import Path

# --- CONFIGURATION ---
BASE_DIR = Path(__file__).resolve().parent
LOG_FILES = [
    BASE_DIR / "Data" / "autonomous_run.log",
    BASE_DIR / "daily_task.log"
]
XLSX_FILE = BASE_DIR / "Data" / "state_of_the_day.xlsx"
TASKS = ["AnalyzeFinData_Morning", "AnalyzeFinData_AI_Game", "AnalyzeFinData_AI_Summary", "AnalyzeFinData_Evening"]

def check_logs():
    """Audit logs for 'Error', 'Failed', or 'Fatal' entries in the last 24h."""
    errors = []
    now = datetime.datetime.now()
    for log_path in LOG_FILES:
        if not log_path.exists():
            continue
        with open(log_path, "r") as f:
            lines = f.readlines()
            for line in lines[-50:]: # Check last 50 lines
                if any(word in line.upper() for word in ["ERROR", "FAILED", "FATAL", "CRASH"]):
                    # Basic date extraction from [YYYY-MM-DD HH:MM:SS]
                    try:
                        log_date_str = line.split("]")[0].strip("[")
                        log_date = datetime.datetime.strptime(log_date_str, "%Y-%m-%d %H:%M:%S")
                        if (now - log_date).total_seconds() < 86400:
                            errors.append(f"[{log_path.name}] {line.strip()}")
                    except:
                        # If no timestamp, just include if recent
                        errors.append(f"[{log_path.name}] {line.strip()}")
    return errors

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
        # Logic to re-create the specific task
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

    # 3. Final Check
    remaining_errors = check_logs()
    
    issues = []
    if remaining_errors: issues.append("REMAINING LOG ERRORS:\n" + "\n".join(remaining_errors))
    if data_issue: issues.append(data_issue)
    
    if recovery_actions or issues:
        report = []
        if recovery_actions: report.append("🤖 AUTONOMOUS RECOVERY ACTIONS:\n" + "\n".join(recovery_actions))
        if issues: report.append("🚨 REMAINING ISSUES:\n" + "\n".join(issues))
        
        alert_msg = "\n\n".join(report)
        print("Healer finished with actions or issues. Sending report...")
        notify.send_email("🛡️ Project AETHER: Health & Recovery Report", alert_msg)
    else:
        print("✅ System Health Check: All systems nominal.")

if __name__ == "__main__":
    run_watchdog()
