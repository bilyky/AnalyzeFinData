"""JsonPortfolioRepository: portfolios stored as JSON files with timestamped backups (ADR 0003).

The default store. It owns each portfolio's file, its backups (the newest 15 are kept) and
restoring from a backup. The file code came as-is from ``ai_portfolio_game.load_game`` /
``save_game`` (#295), then:

- **F1 (fixed):** a save backs the file up first and **stops if that backup fails**. The file
  is then written atomically: to a temp file in the same folder, flushed to disk, and swapped
  in with ``os.replace``. Windows refuses the swap while another process has the file open
  (for example the dashboard reading it), so the swap is retried for a few seconds. If any
  step fails, the file keeps its last good content, the state goes to
  ``<file>.unsaved-<time>``, an ERROR is logged and ``StorageError`` is raised.
- **F2 (fixed):** a fresh portfolio only on a true first run: no file **and** no backup. A
  missing, empty or corrupt file is restored from the newest readable backup. If none can be
  read, the load stops: an ERROR is logged and ``StorageError`` is raised, and nothing is
  written. Before F2 the game silently started again with $10k cash. A corrupt file is kept as
  ``<file>.corrupt-<time>`` before a backup is copied over it.

Which file belongs to which portfolio is configuration, passed in by the composition root
(``aether.bootstrap``); callers of the service never see a path.
"""
import datetime
import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from aether import trash
from aether.core import PortfolioId, StorageError
from aether.logger import get_logger
from aether.services.portfolio.adapters import json_mapper
from aether.services.portfolio.domain.model import Portfolio
from aether.services.portfolio.errors import UnknownPortfolio

KEEP_BACKUPS = 15
REPLACE_TRIES = 10       # os.replace attempts while a reader holds the file open
REPLACE_WAIT_S = 0.5     # pause between attempts (about 5 s in all)
RUNBOOK = "aether/services/portfolio/README.md (When a run logs NOT saved)"
LOAD_RUNBOOK = "aether/services/portfolio/README.md (When the state cannot be loaded)"


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
        """The stored portfolio; a fresh one only on a true first run (no file, no backup)."""
        state = self._read(portfolio_id)
        if state is None:
            return Portfolio.new(portfolio_id, self.starting_capital, str(datetime.date.today()))
        return json_mapper.from_dict(portfolio_id, state)

    def save(self, portfolio: Portfolio) -> None:
        self._write(portfolio.id, json_mapper.to_dict(portfolio))

    def quarantine(self, portfolio_id: PortfolioId, state) -> str:
        """Write a state that could not be saved next to its file, untouched; returns the path."""
        f = self._file(portfolio_id)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        target = Path(f.path).with_name(f"{Path(f.path).name}.unsaved-{ts}")
        with open(target, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=4, default=repr)
        return str(target)

    # -- the file (today's load_game / save_game, then F1 and F2) -------------------------
    def _backups(self, f: PortfolioFile) -> list:
        backup_dir = Path(f.backup_dir)
        if not backup_dir.exists():
            return []
        return sorted(backup_dir.glob(f.backup_glob), key=lambda x: x.stat().st_mtime, reverse=True)

    def _restore_from_backup(self, f: PortfolioFile, success_message: str):
        """Newest readable backup, copied back over the file; None if there is none."""
        for b in self._backups(f):
            try:
                with open(b, "r", encoding="utf-8") as bf:
                    state = json.load(bf)
                    shutil.copy2(b, f.path)
                    self._log.warning(success_message.format(name=b.name))
                    return state
            except Exception:
                continue
        return None

    def _keep_corrupt_copy(self, path: Path) -> None:
        """Keep the unreadable file as evidence before a backup is copied over it."""
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        try:
            shutil.copy2(path, path.with_name(f"{path.name}.corrupt-{ts}"))
        except Exception as e:
            self._log.warning(f"  [PORTFOLIO] Could not keep a copy of the unreadable {path.name}: {e}")

    def _refuse_load(self, portfolio_id: PortfolioId, f: PortfolioFile, why: str, cause=None):
        n = len(self._backups(f))
        msg = (f"state for {portfolio_id} cannot be loaded: {why}, and none of the {n} backups in "
               f"{f.backup_dir} can be read. Nothing was written. To recover, see {LOAD_RUNBOOK}")
        self._log.error(f"  [PORTFOLIO] {msg}.")
        raise StorageError(msg) from cause

    def _read(self, portfolio_id: PortfolioId):
        """The stored dict, or None on a true first run (no file and no backup)."""
        f = self._file(portfolio_id)
        path = Path(f.path)
        if not path.exists() or path.stat().st_size == 0:
            missing = not path.exists()
            if missing and not self._backups(f):
                return None  # a true first run: never saved, nothing to restore
            state = self._restore_from_backup(
                f, "  [⚠️ AETHER SELF-HEALER] AI game file was missing or empty. "
                   "Successfully restored from backup: {name}")
            if state is None:
                self._refuse_load(portfolio_id, f, f"{path.name} is {'missing' if missing else 'empty'}")
            return state
        with open(path, "r", encoding="utf-8") as fh:
            try:
                return json.load(fh)
            except Exception as e:
                error = e
        self._log.warning(f"  [⚠️ AETHER SELF-HEALER] Error loading {path.name}: {error}. "
                          "Attempting automated recovery from backup...")
        self._keep_corrupt_copy(path)
        state = self._restore_from_backup(
            f, "  [⚠️ AETHER SELF-HEALER] Successfully restored corrupted file from backup: {name}")
        if state is None:
            self._refuse_load(portfolio_id, f, f"{path.name} is not valid JSON ({error})", error)
        return state

    def _write(self, portfolio_id: PortfolioId, state: dict) -> None:
        f = self._file(portfolio_id)
        path, backup_dir = Path(f.path), Path(f.backup_dir)
        # --- Mandatory Backup before Write (F1: no backup, no write) ---
        if path.exists():
            try:
                backup_dir.mkdir(parents=True, exist_ok=True)
                ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                shutil.copy2(path, backup_dir / f.backup_name(ts))
            except Exception as e:
                self._refuse(portfolio_id, state, f"the backup of {path.name} failed ({e})", e)
            try:
                # Clean up old backups (keep the last KEEP_BACKUPS); the new backup exists, so a
                # failed prune does not put the state at risk.
                backups = sorted(list(backup_dir.glob(f.backup_glob)), key=lambda x: x.stat().st_mtime)
                if len(backups) > KEEP_BACKUPS:
                    for old_b in backups[:-KEEP_BACKUPS]:
                        trash.soft_delete(old_b, reason="game-backup-prune", force=True)
            except Exception as e:
                self._log.warning(f"  [Warning] Game backup prune failed: {e}")
        self._atomic_write(portfolio_id, path, state)

    def _atomic_write(self, portfolio_id: PortfolioId, path: Path, state: dict) -> None:
        """Write to a temp file in the same folder, then swap it in; the file is never half-written."""
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:  # text mode: the same bytes as before
                json.dump(state, fh, indent=4)
                fh.flush()
                os.fsync(fh.fileno())
        except Exception as e:
            _remove_quietly(tmp)
            self._refuse(portfolio_id, state, f"writing {path.name} failed ({e})", e)
        for attempt in range(1, REPLACE_TRIES + 1):
            try:
                os.replace(tmp, path)
                return
            except PermissionError as e:  # Windows: another process has the file open
                if attempt == REPLACE_TRIES:
                    _remove_quietly(tmp)
                    self._refuse(portfolio_id, state, f"{path.name} stayed locked by another process ({e})", e)
                time.sleep(REPLACE_WAIT_S)
            except Exception as e:
                _remove_quietly(tmp)
                self._refuse(portfolio_id, state, f"replacing {path.name} failed ({e})", e)

    def _refuse(self, portfolio_id: PortfolioId, state, why: str, cause: Exception):
        """Keep the state in a quarantine file, log an ERROR and raise; the stored file is untouched."""
        try:
            where = self.quarantine(portfolio_id, state)
        except Exception as q:
            where = f"nowhere (the quarantine write failed: {q})"
        self._log.error(f"  [PORTFOLIO] State for {portfolio_id} NOT saved: {why}. "
                        f"The file keeps its last good state. Unsaved state kept in: {where}. "
                        f"To recover before the next run, see {RUNBOOK}.")
        raise StorageError(f"state for {portfolio_id} not saved: {why}; unsaved copy: {where}") from cause


def _remove_quietly(path) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
