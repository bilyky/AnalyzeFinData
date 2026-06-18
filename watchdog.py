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

def run_watchdog():
    print(f"[{datetime.datetime.now()}] Project AETHER Watchdog starting...")
    
    log_errors = check_logs()
    missing_tasks = check_task_scheduler()
    data_issue = check_data_freshness()
    
    issues = []
    if log_errors: issues.append("LOG ERRORS FOUND:\n" + "\n".join(log_errors))
    if missing_tasks: issues.append("MISSING/FAILED TASKS:\n" + ", ".join(missing_tasks))
    if data_issue: issues.append(data_issue)
    
    if issues:
        alert_msg = "\n\n".join(issues)
        print("🚨 Watchdog identified issues! Sending alert...")
        notify.send_email("🚨 Project AETHER: System Health Alert", 
                          f"Watchdog has identified the following issues in the last 24h:\n\n{alert_msg}")
    else:
        print("✅ System Health Check: All systems nominal.")

if __name__ == "__main__":
    run_watchdog()
