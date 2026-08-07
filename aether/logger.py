"""
Centralised logging for Project AETHER.

Three output channels:
  1. Rotating plain-text  — Data/logs/aether.log       (5 MB × 5 backups, human-readable)
  2. Rotating JSON Lines  — Data/logs/aether.jsonl      (5 MB × 5 backups, grep/parse friendly)
  3. Stderr               — INFO+ in development, WARNING+ in prod (respects LOG_LEVEL env var)

Usage:
    from aether_logger import get_logger
    log = get_logger(__name__)
    log.info("Pipeline started")
    log.warning("Stale data", extra={"age_days": 77})
    log.error("E*TRADE timeout", exc_info=True)

All three channels share the same Logger hierarchy — call get_logger() with any
name; loggers under 'aether.*' are automatically routed through the AETHER handlers.
"""

import json
import logging
import logging.handlers
import os
import sys
import time
from pathlib import Path

_BASE_DIR = Path(__file__).resolve().parent.parent
_LOG_DIR = _BASE_DIR / "Data" / "logs"

# Max 5 MB per file, keep 5 rotated backups = max 30 MB total per channel
_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 5

_TEXT_FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATE_FMT = "%Y-%m-%d %H:%M:%S"

_initialised = False

# ── Custom CONSOLE level ─────────────────────────────────────────────────────
# Between DEBUG (10) and INFO (20). Writes to stdout only — not to log files.
# Use for interactive progress messages that are useful on screen but should not
# clutter aether.log / aether.jsonl (e.g. "Scanning mailbox X...", "3/500 done").
CONSOLE = 15
logging.addLevelName(CONSOLE, "CONSOLE")


def _console(self, message, *args, **kwargs):
    if self.isEnabledFor(CONSOLE):
        self._log(CONSOLE, message, args, **kwargs)


logging.Logger.console = _console


class _JsonlFormatter(logging.Formatter):
    """One JSON object per line, safe to stream-parse."""

    def format(self, record: logging.LogRecord) -> str:
        obj: dict = {
            "ts": self.formatTime(record, _DATE_FMT),
            "level": record.levelname,
            "module": record.name,
            "msg": record.getMessage(),
        }
        # Structured extras passed via log.info("...", extra={"key": val})
        _INTERNAL = frozenset({
            "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
            "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
            "created", "msecs", "relativeCreated", "thread", "threadName",
            "processName", "process", "message", "asctime", "taskName",
        })
        extras = {k: v for k, v in record.__dict__.items()
                  if k not in _INTERNAL and not k.startswith("_")}
        if extras:
            obj["extra"] = extras
        if record.exc_info:
            obj["exc"] = self.formatException(record.exc_info)
        return json.dumps(obj, default=str)


def _init():
    global _initialised
    if _initialised:
        return
    _initialised = True

    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger("aether")
    if root.handlers:          # already configured (e.g. in tests)
        return
    root.setLevel(logging.DEBUG)

    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    stdout_level = getattr(logging, level_name, logging.INFO)

    # ── 1. Rotating plain text — INFO+ only (no CONSOLE noise) ─────────────
    txt_handler = logging.handlers.RotatingFileHandler(
        _LOG_DIR / "aether.log",
        maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8",
    )
    txt_handler.setLevel(logging.INFO)   # CONSOLE(15) is below INFO(20) — excluded
    txt_handler.setFormatter(logging.Formatter(_TEXT_FMT, _DATE_FMT))
    root.addHandler(txt_handler)

    # ── 2. Rotating JSON Lines — INFO+ only ─────────────────────────────────
    jsonl_handler = logging.handlers.RotatingFileHandler(
        _LOG_DIR / "aether.jsonl",
        maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8",
    )
    jsonl_handler.setLevel(logging.INFO)  # CONSOLE(15) excluded from structured log
    jsonl_handler.setFormatter(_JsonlFormatter())
    root.addHandler(jsonl_handler)

    # ── 3. Stderr — include CONSOLE(15) when env level is INFO or lower ──────
    # Diagnostics/progress go to STDERR (Unix convention) so a script's real
    # output on stdout — e.g. `compare_stocks.py --json` — stays clean and
    # `2>/dev/null` actually silences the logs. Terminal users still see these
    # (stderr prints to the console); console_safe wraps stderr too, so emoji
    # in log lines don't crash on Windows.
    # If LOG_LEVEL=WARNING/ERROR, respect that — suppress CONSOLE noise.
    # If LOG_LEVEL=INFO/DEBUG (default), also show CONSOLE progress messages.
    stderr_level_eff = CONSOLE if stdout_level <= logging.INFO else stdout_level
    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setLevel(stderr_level_eff)
    stream_handler.setFormatter(logging.Formatter(_TEXT_FMT, _DATE_FMT))
    root.addHandler(stream_handler)


def get_logger(name: str = "aether") -> logging.Logger:
    """Return a logger under the 'aether' hierarchy.

    If `name` is already 'aether' or starts with 'aether.', it is used as-is.
    Otherwise it is prefixed so all project loggers are routed through the
    same handler chain (e.g. get_logger(__name__) from 'server' → 'aether.server').
    """
    _init()
    if name == "aether" or name.startswith("aether."):
        return logging.getLogger(name)
    return logging.getLogger(f"aether.{name}")


# Module-level convenience logger
log = get_logger("aether")
