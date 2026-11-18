"""The game state as a JSON file with timestamped backups (R&D #78 S4a).

Moved as-is from ``ai_portfolio_game.load_game`` / ``save_game``; the behaviour is unchanged,
including two known gaps that F1 and F2 fix next, each in its own PR:

- **F1:** ``save`` still writes when the backup copy fails, and the write is not atomic.
- **F2:** a corrupt file with no readable backup quietly becomes a fresh starting state.

The paths, the logger and the starting balance are passed in, so the caller (today the
game script) keeps owning its configuration and the test harness redirect keeps working.
"""
import datetime
import json
import shutil
from pathlib import Path

from aether import trash
from aether.logger import get_logger

BACKUP_GLOB = "ai_portfolio_game_*.json"
KEEP_BACKUPS = 15


class JsonStateFile:
    """``StateStore`` over one JSON file and its backup folder."""

    def __init__(self, path: Path, backup_dir: Path, *, initial_balance: float, log=None):
        self.path = Path(path)
        self.backup_dir = Path(backup_dir)
        self.initial_balance = initial_balance
        self._log = log or get_logger("ai_game")

    def _fresh_state(self) -> dict:
        return {
            "balance": self.initial_balance,
            "equity": self.initial_balance,
            "positions": {},
            "history": [],
            "start_date": str(datetime.date.today()),
            "profile": "BALANCED"
        }

    def _restore_from_backup(self, success_message: str):
        """Newest readable backup, copied back over the state file; None if there is none."""
        if self.backup_dir.exists():
            backups = sorted(list(self.backup_dir.glob(BACKUP_GLOB)), key=lambda x: x.stat().st_mtime, reverse=True)
            for b in backups:
                try:
                    with open(b, "r", encoding="utf-8") as bf:
                        state = json.load(bf)
                        shutil.copy2(b, self.path)
                        self._log.warning(success_message.format(name=b.name))
                        return state
                except Exception:
                    continue
        return None

    def load(self) -> dict:
        if not self.path.exists() or self.path.stat().st_size == 0:
            state = self._restore_from_backup(
                "  [⚠️ AETHER SELF-HEALER] AI game file was missing or empty. "
                "Successfully restored from backup: {name}")
            return state if state is not None else self._fresh_state()
        with open(self.path, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except Exception as e:
                self._log.warning(f"  [⚠️ AETHER SELF-HEALER] Error loading {self.path.name}: {e}. "
                                  "Attempting automated recovery from backup...")
                state = self._restore_from_backup(
                    "  [⚠️ AETHER SELF-HEALER] Successfully restored corrupted file from backup: {name}")
                # Fallback to default state if all else fails (F2 changes this)
                return state if state is not None else self._fresh_state()

    def save(self, state: dict) -> None:
        # --- Mandatory Backup before Write ---
        if self.path.exists():
            try:
                self.backup_dir.mkdir(parents=True, exist_ok=True)
                ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                backup_path = self.backup_dir / f"ai_portfolio_game_{ts}.json"
                shutil.copy2(self.path, backup_path)
                # Clean up old backups (keep the last KEEP_BACKUPS)
                backups = sorted(list(self.backup_dir.glob(BACKUP_GLOB)), key=lambda x: x.stat().st_mtime)
                if len(backups) > KEEP_BACKUPS:
                    for old_b in backups[:-KEEP_BACKUPS]:
                        trash.soft_delete(old_b, reason="game-backup-prune", force=True)
            except Exception as e:
                self._log.warning(f"  [Warning] Game backup failed: {e}")  # F1: abort instead

        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=4)
