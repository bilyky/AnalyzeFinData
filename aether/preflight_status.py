"""Today's morning pre-flight verdict, kept for the 07:00 trade run.

The morning pipeline (autonomous_pipeline.py) records whether its pre-flight passed. The trade run reads
it: after a failed pre-flight it makes no buys (exits still run), unless it was started by hand with
--force. PROD 2026-11-09: the pipeline aborted on an expired Chaikin session, yet the trade run bought
on Sunday's workbook (PROD review 11-09, owner decision).

Only the morning pipeline writes this file. The 21:30 audit and manual preflight_validator.py runs don't.
"""
import datetime
import json
import os
from pathlib import Path

from aether import paths
from aether.logger import get_logger as _get_logger

_log = _get_logger("preflight_status")

STATUS_FILE = Path(paths.data_dir()) / "preflight_status.json"


def record(passed: bool, today: str | None = None) -> None:
    """Write today's verdict. A later pipeline re-run that passes overwrites a failure."""
    today = today or str(datetime.date.today())
    rec = {"date": today, "passed": bool(passed), "at": datetime.datetime.now().isoformat()}
    try:
        STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATUS_FILE.with_name(STATUS_FILE.name + ".tmp")
        tmp.write_text(json.dumps(rec), encoding="utf-8")
        os.replace(tmp, STATUS_FILE)
    except OSError as e:
        _log.error(f"Could not record the pre-flight verdict ({rec}): {e}")


def failed_on(today: str) -> bool:
    """True when the recorded pre-flight for ``today`` failed, or the record can't be read.

    No record, or a record from another day, is not a failure: the stale-workbook gate covers a
    pipeline that did not run at all.
    """
    if not STATUS_FILE.exists():
        return False
    try:
        rec = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        if rec.get("date") != today:
            return False
        return rec.get("passed") is not True
    except (OSError, ValueError, AttributeError) as e:
        _log.warning(f"Pre-flight verdict {STATUS_FILE} is unreadable ({e}); treating today's pre-flight as failed.")
        return True
