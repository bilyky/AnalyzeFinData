"""Development > Project Model: progress of the AETHER services redesign (R&D #75).

Two parts:

* the plan's progress, read from ``plans/aether-services-progress.json`` (steps, fixes and
  services, each with a status and PR). That file is updated by hand in every R&D #75 PR;
* live code metrics measured from the source tree on every call, so the page shows what the
  code actually looks like, not what anyone claims about it.
"""
import json
import re
from pathlib import Path

PROGRESS_FILE = Path("plans") / "aether-services-progress.json"

_GAME_ATTR = re.compile(r"\bgame\.[A-Za-z_]\w*")
_MAGIC_ROW = re.compile(r"\brow\[\d+\]")
_PKG_DEF = re.compile(r"^def _pkg\(", re.MULTILINE)


def _py_files(root: Path, rel: str):
    base = root / rel
    if base.is_file():
        return [base]
    if base.is_dir():
        return sorted(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)
    return []


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _line_count(paths) -> int:
    return sum(len(_read(p).splitlines()) for p in paths)


def code_metrics(root) -> dict:
    """Size and coupling numbers for the daily-run code; each should shrink as the plan lands."""
    root = Path(root)
    game = _py_files(root, "ai_portfolio_game.py")
    scenario = _py_files(root, "aether/scenario")
    services = _py_files(root, "aether/services")
    run_code = game + scenario + services
    return {
        "root_file_lines": _line_count(game),
        "scenario_lines": _line_count(scenario),
        "services_lines": _line_count(services),
        "game_attr_calls": sum(len(_GAME_ATTR.findall(_read(p))) for p in scenario + services),
        "magic_row_indexes": sum(len(_MAGIC_ROW.findall(_read(p))) for p in run_code),
        # Only the daily-run code: aether/etrade has its own _pkg() and is out of R&D #75's scope.
        "pkg_shims": sum(len(_PKG_DEF.findall(_read(p))) for p in scenario + services),
    }


def load_progress(root) -> dict:
    """The progress file as a dict; raises if it is missing or not valid JSON (fail loudly)."""
    path = Path(root) / PROGRESS_FILE
    return json.loads(_read(path))


def _summary(items) -> dict:
    counts = {}
    for item in items:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return counts


def project_model(root) -> dict:
    """Everything the Project Model page shows."""
    progress = load_progress(root)
    return {
        **progress,
        "summary": {"steps": _summary(progress["steps"]), "fixes": _summary(progress["fixes"])},
        "metrics": code_metrics(root),
    }
