"""Entry point for the AETHER_Data_Backup scheduled task: the ONE place Data/ is backed up.

Runs once a day (the time is in scripts/utils/scheduled_tasks.json), after the day's data
collection. If a data-collecting script is still running (the evening daily_task and its
children, or a late morning pipeline), it waits for it to finish, polling every
WAIT_STEP_S seconds for up to MAX_WAIT_MIN minutes. Past that it backs up anyway and says so:
one slightly inconsistent copy beats no copy.

Exits 1 when the backup did not happen (sync error, or the share offline), so Task Scheduler
and the watchdog see a failed run instead of a silent success.
"""

import os
import subprocess
import sys
import time

# The scheduler runs this file directly, which puts only scripts/utils on sys.path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import watchdog
from aether_logger import get_logger

_log = get_logger("data_backup")

# Scripts that write Data/ while they run.
COLLECTOR_SCRIPTS = ("daily_task.py", "autonomous_pipeline.py", "main.py", "run_history.py", "rapidapi.py")
WAIT_STEP_S = 60
MAX_WAIT_MIN = 120


def running_collectors() -> list[str]:
    """Collector scripts with a live python process (this process excluded). On any error the
    answer is "none", so a broken process query never blocks the backup."""
    query = ("Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
             "ForEach-Object { \"$($_.ProcessId)|$($_.CommandLine)\" }")
    try:
        res = subprocess.run(["powershell.exe", "-NoProfile", "-Command", query], capture_output=True,
                             text=True, errors="replace", stdin=subprocess.DEVNULL, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return []
    found = set()
    for line in (res.stdout or "").splitlines():
        pid, _, cmdline = line.partition("|")
        if pid.strip() == str(os.getpid()):
            continue
        tokens = cmdline.replace("\\", "/").replace('"', " ").split()
        for script in COLLECTOR_SCRIPTS:
            if any(tok == script or tok.endswith("/" + script) for tok in tokens):
                found.add(script)
    return sorted(found)


def wait_for_collection() -> None:
    deadline = time.monotonic() + MAX_WAIT_MIN * 60
    polls = MAX_WAIT_MIN * 60 // WAIT_STEP_S
    for _ in range(polls + 1):
        busy = running_collectors()
        if not busy:
            return
        if time.monotonic() >= deadline:
            break
        _log.info(f"Data collection still running ({', '.join(busy)}); waiting {WAIT_STEP_S}s before the backup.")
        time.sleep(WAIT_STEP_S)
    _log.warning(f"Data collection still running after {MAX_WAIT_MIN} min ({', '.join(busy)}); backing up anyway.")


def main() -> int:
    wait_for_collection()
    before = watchdog.backup_status().get("last_attempt")
    ok = watchdog.sync_data_folder()
    status = watchdog.backup_status()
    if status.get("last_attempt") == before:
        # sync_data_folder() returns True without recording anything during market hours.
        _log.error("Data backup was skipped (no attempt recorded; inside market hours?).")
        return 1
    if not ok or status.get("last_result") != "ok":
        _log.error(f"Data backup did not complete (result: {status.get('last_result')}).")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
