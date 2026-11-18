"""Public interface of the portfolio service: the only module other code may import.

API_VERSION follows semver; a breaking change bumps the major version (ADR 0004).
"""
from pathlib import Path

from aether.services.portfolio.adapters.json_file import JsonStateFile
from aether.services.portfolio.ports import StateStore

API_VERSION = "0.1.0"

__all__ = ["API_VERSION", "StateStore", "open_state_file"]


def open_state_file(path: Path, backup_dir: Path, *, initial_balance: float, log=None) -> StateStore:
    """The JSON state store for one portfolio file and its backup folder."""
    return JsonStateFile(path, backup_dir, initial_balance=initial_balance, log=log)
