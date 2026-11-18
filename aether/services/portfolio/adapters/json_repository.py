"""JsonPortfolioRepository: portfolios stored as JSON files with timestamped backups (ADR 0003).

The default store. It owns each portfolio's file, its backups (the newest 15 are kept) and
restoring from a backup. The file code is moved as-is from ``ai_portfolio_game.load_game`` /
``save_game``; the behaviour is unchanged, including two known gaps that the next PRs fix:

- **F1:** ``save`` still writes when the backup copy fails, and the write is not atomic.
- **F2:** a corrupt file with no readable backup quietly becomes a fresh portfolio.

Which file belongs to which portfolio is configuration, passed in by the composition root
(``aether.bootstrap``); callers of the service never see a path.
"""
import datetime
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from aether import trash
from aether.core import PortfolioId
from aether.logger import get_logger
from aether.services.portfolio.adapters import json_mapper
from aether.services.portfolio.domain.model import Portfolio
from aether.services.portfolio.errors import UnknownPortfolio

KEEP_BACKUPS = 15


@dataclass(frozen=True)
class PortfolioFile:
    """Where one portfolio is stored: its JSON file and its backup folder."""
    path: Path
    backup_dir: Path

    @property
    def backup_glob(self) -> str:
        return f"{Path(self.path).stem}_*.json"  # ai_portfolio_game_*.json for the game

    def backup_name(self, ts: str) -> str:
        return f"{Path(self.path).stem}_{ts}.json"


class JsonPortfolioRepository:
    """``PortfolioRepository`` over JSON files, one per portfolio."""

    def __init__(self, files: dict, *, starting_capital: float, log=None):
        self._files = {PortfolioId(str(k)) if not isinstance(k, PortfolioId) else k: v for k, v in files.items()}
        self.starting_capital = starting_capital
        self._log = log or get_logger("ai_game")

    def _file(self, portfolio_id: PortfolioId) -> PortfolioFile:
        try:
            return self._files[portfolio_id]
        except KeyError:
            raise UnknownPortfolio(f"no store configured for portfolio {portfolio_id}") from None

    # -- PortfolioRepository ---------------------------------------------------------------
    def load(self, portfolio_id: PortfolioId) -> Portfolio:
        """The stored portfolio; a fresh one on a first run (and, until F2, on an unreadable file)."""
        state = self._read(portfolio_id)
        if state is None:
            return Portfolio.new(portfolio_id, self.starting_capital, str(datetime.date.today()))
        return json_mapper.from_dict(portfolio_id, state)

    def save(self, portfolio: Portfolio) -> None:
        self._write(portfolio.id, json_mapper.to_dict(portfolio))

    # -- the file (today's load_game / save_game, moved as-is) -----------------------------
    def _restore_from_backup(self, f: PortfolioFile, success_message: str):
        """Newest readable backup, copied back over the file; None if there is none."""
        backup_dir = Path(f.backup_dir)
        if backup_dir.exists():
            backups = sorted(list(backup_dir.glob(f.backup_glob)), key=lambda x: x.stat().st_mtime, reverse=True)
            for b in backups:
                try:
                    with open(b, "r", encoding="utf-8") as bf:
                        state = json.load(bf)
                        shutil.copy2(b, f.path)
                        self._log.warning(success_message.format(name=b.name))
                        return state
                except Exception:
                    continue
        return None

    def _read(self, portfolio_id: PortfolioId):
        """The stored dict, or None when a fresh portfolio is due."""
        f = self._file(portfolio_id)
        path = Path(f.path)
        if not path.exists() or path.stat().st_size == 0:
            state = self._restore_from_backup(
                f, "  [⚠️ AETHER SELF-HEALER] AI game file was missing or empty. "
                   "Successfully restored from backup: {name}")
            return state
        with open(path, "r", encoding="utf-8") as fh:
            try:
                return json.load(fh)
            except Exception as e:
                self._log.warning(f"  [⚠️ AETHER SELF-HEALER] Error loading {path.name}: {e}. "
                                  "Attempting automated recovery from backup...")
                state = self._restore_from_backup(
                    f, "  [⚠️ AETHER SELF-HEALER] Successfully restored corrupted file from backup: {name}")
                # Fallback to default state if all else fails (F2 changes this)
                return state

    def _write(self, portfolio_id: PortfolioId, state: dict) -> None:
        f = self._file(portfolio_id)
        path, backup_dir = Path(f.path), Path(f.backup_dir)
        # --- Mandatory Backup before Write ---
        if path.exists():
            try:
                backup_dir.mkdir(parents=True, exist_ok=True)
                ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                shutil.copy2(path, backup_dir / f.backup_name(ts))
                # Clean up old backups (keep the last KEEP_BACKUPS)
                backups = sorted(list(backup_dir.glob(f.backup_glob)), key=lambda x: x.stat().st_mtime)
                if len(backups) > KEEP_BACKUPS:
                    for old_b in backups[:-KEEP_BACKUPS]:
                        trash.soft_delete(old_b, reason="game-backup-prune", force=True)
            except Exception as e:
                self._log.warning(f"  [Warning] Game backup failed: {e}")  # F1: abort instead

        with open(path, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=4)
