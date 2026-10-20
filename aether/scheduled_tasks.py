"""The scheduled-task manifest (scripts/utils/scheduled_tasks.json): the one list of AETHER tasks.

register_agent_tasks.ps1 registers exactly these, the watchdog audits them, and the System page
shows them with their description and parameters. Nothing else should keep its own task list.
"""
import json
import os
from pathlib import Path

# AETHER_TASK_MANIFEST points at another file (tests use it to prove a broken manifest is
# reported, not fatal); unset means the repo's manifest.
MANIFEST = Path(os.environ.get("AETHER_TASK_MANIFEST")
                or Path(__file__).resolve().parent.parent / "scripts" / "utils" / "scheduled_tasks.json")
PYTHON = r"venv_new\Scripts\python.exe"
AGENT_LAUNCHER = "run_agent.cmd"
_SCHEDULE_TYPES = {"hourly", "daily", "weekly"}
_DAYS = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}


def load(path: Path | None = None) -> dict:
    path = path or MANIFEST
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    problems = validate(data)
    if problems:
        raise ValueError(f"{path}: " + "; ".join(problems))
    return data


def validate(data: dict) -> list[str]:
    """Problems with the manifest's shape (empty list when it is fine)."""
    out = []
    names = [t.get("name") for t in data.get("tasks", [])]
    if len(names) != len(set(names)):
        out.append("duplicate task names")
    for t in data.get("tasks", []):
        n = t.get("name") or "<no name>"
        for key in ("schedule", "log", "summary", "description", "time_limit_min"):
            if key not in t:
                out.append(f"{n}: missing {key}")
        if ("script" in t) == ("prompt" in t):
            out.append(f"{n}: needs exactly one of script / prompt")
        s = t.get("schedule", {})
        if s.get("type") not in _SCHEDULE_TYPES:
            out.append(f"{n}: unknown schedule type {s.get('type')!r}")
        if s.get("type") in ("daily", "weekly") and not s.get("at"):
            out.append(f"{n}: schedule needs 'at'")
        if s.get("type") == "weekly" and s.get("day") not in _DAYS:
            out.append(f"{n}: weekly schedule needs a day")
    return out


def tasks(path: Path | None = None) -> list[dict]:
    return load(path)["tasks"]


def full_names(path: Path | None = None) -> list[str]:
    """Task Scheduler paths, e.g. \\AETHER_Agents\\AETHER_Watchdog."""
    data = load(path)
    return [f"{data['folder']}{t['name']}" for t in data["tasks"]]


def schedule_text(task: dict) -> str:
    s = task["schedule"]
    if s["type"] == "hourly":
        return "Hourly, 24/7"
    base = f"Daily {s['at']}" if s["type"] == "daily" else f"Weekly {s['day']} {s['at']}"
    if s.get("repeat_every_min"):
        base += f", every {s['repeat_every_min']} min for {s['repeat_for_hours']} h"
    return base


def command_text(task: dict) -> str:
    """What the task runs, as the registrar builds it (before the log redirect)."""
    if "script" in task:
        return f"{PYTHON} {task['script']} {task.get('args', '')}".rstrip()
    return f'{AGENT_LAUNCHER} <engine> <engine args> -p "{task["prompt"]}"'


def describe(task: dict) -> dict:
    """The task's manifest fields plus the readable schedule and command, for the System page."""
    return {
        "name": task["name"],
        "summary": task["summary"],
        "description": task["description"],
        "notes": task.get("notes", ""),
        "kind": "python" if "script" in task else "agent",
        "schedule": schedule_text(task),
        "command": command_text(task),
        "time_limit_min": task["time_limit_min"],
        "log": task["log"],
        "run_as": "interactive (only when logged on)" if task.get("interactive") else "default",
        "manual_run": task.get("manual_run"),
    }
