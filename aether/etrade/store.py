"""Persistence layer for the E*TRADE service — Ports & Adapters (hexagonal).

WHY THIS EXISTS
---------------
Today every piece of E*TRADE state lives in a raw JSON file under ``Data/``:

    Data/etrade_tokens.json          OAuth access token + secret (SECRET)
    Data/etrade_browser_state.json   Playwright trusted-device cookies (SECRET)
    Data/etrade_reauth_state.json    re-auth circuit-breaker counters (non-secret)

That is fine for a single process on one box, but it blocks (a) a clean migration
to a shared database and (b) running the auth plane and a scaled data plane as
separate k8s pods. This module defines **ports** (abstract interfaces) for each
kind of state plus a **file adapter** (today's behaviour) and a seam where a
``store_db`` adapter can be dropped in later — selected by ``make_etrade_store``
from ``DATABASE_URL`` with *zero* call-site changes.

DESIGN NOTES
------------
* The file adapters **delegate to the proven free functions** in ``aether.etrade``
  (``_load_tokens`` / ``_save_tokens`` / ``_load_reauth_state`` …) rather than
  re-implementing the JSON shapes. That guarantees the file backend can never
  drift from the battle-tested (IP-ban-hardened) core, and — critically — it
  inherits the core's two non-negotiable test seams for free:
    - path constants (``_TOKEN_PATH`` …) are read **at call time**, so a test that
      reassigns ``etrade._TOKEN_PATH`` redirects the store too;
    - deletion goes through the package's ``os`` object, so ``mock.patch.object(
      etrade.os, "remove")`` still intercepts it.
* **Secrets never go in a plain DB table.** The DB adapter (future) persists only
  the *non-secret* rows — circuit-breaker state, the audit log, position
  snapshots. OAuth secrets and browser state stay in a k8s Secret / envelope-
  encrypted blob. The port split below marks each store SECRET or SHAREABLE.
* Adapters touch the package lazily (inside methods), never at import time, so
  ``aether.etrade.__init__`` can import this module from its own bottom without a
  circular-import hazard.
"""
from __future__ import annotations

import abc
import json
import os
import time
from typing import Optional


def _pkg():
    """Return the fully-initialised ``aether.etrade`` package (lazy, call-time).

    Imported inside methods so this module can be imported *from* the package's
    ``__init__`` without a partially-initialised-module problem.
    """
    from aether import etrade as _p
    return _p


# ===========================================================================
# Ports (abstract interfaces) + their record schemas
# ===========================================================================

class TokenStore(abc.ABC):
    """OAuth access token + secret.  **SECRET — never persisted to a plain DB table.**

    Record schema::

        env             str    "sandbox" | "production"
        oauth_token     str    OAuth1 access token          (SECRET)
        oauth_token_secret str OAuth1 access token secret   (SECRET)
        saved_at        float  epoch seconds of last write/renew
        issued_date_et  str    ISO date (America/New_York) the token was issued
        generation      int    monotonically increasing version (rotation guard)
    """

    @abc.abstractmethod
    def load(self, env: str) -> Optional[dict]:
        """Today's token for ``env`` (same-day ET guard), else None."""

    @abc.abstractmethod
    def load_any_date(self, env: str) -> Optional[dict]:
        """Token for ``env`` regardless of issue date (for renewal attempts)."""

    @abc.abstractmethod
    def save(self, tokens: dict, env: str) -> None:
        """Persist tokens, stamping env / saved_at / issued_date_et."""

    @abc.abstractmethod
    def delete(self) -> None:
        """Remove the cached token (e.g. on explicit 401/403 rejection)."""


class BrowserStateStore(abc.ABC):
    """Playwright storage_state (trusted-device cookies).  **SECRET.**

    Record schema: opaque Playwright ``storage_state`` blob (cookies + origins).
    """

    @abc.abstractmethod
    def exists(self) -> bool: ...

    @abc.abstractmethod
    def load(self) -> Optional[dict]: ...

    @abc.abstractmethod
    def save(self, blob: dict) -> None: ...


class ReauthStateStore(abc.ABC):
    """Automated-reauth circuit-breaker counters.  **SHAREABLE (non-secret).**

    Record schema (per env)::

        env                  str
        consecutive_failures int
        last_attempt         float  epoch seconds
        cooldown_until       float  epoch seconds (0 = gate open)
    """

    @abc.abstractmethod
    def load(self, env: str = "production") -> dict: ...

    @abc.abstractmethod
    def save(self, state: dict, env: str = "production") -> None: ...

    @abc.abstractmethod
    def reset(self, env: str = "production") -> None: ...


class LockProvider(abc.ABC):
    """Cross-process/pod mutual exclusion (so only ONE browser ever opens).

    File adapter uses an O_EXCL lockfile now; a DB row-lease adapter can preserve
    the same single-writer guarantee across pods later.
    """

    @abc.abstractmethod
    def acquire(self, name: str, ttl: float = 30.0) -> bool: ...

    @abc.abstractmethod
    def release(self, name: str) -> None: ...


class AuthEventLog(abc.ABC):
    """Append-only, **secret-free** audit trail of the auth lifecycle. SHAREABLE.

    Record schema (one JSON object per line)::

        ts      float  epoch seconds
        env     str
        event   str    issued|renewed|revoked|probe_ok|probe_fail|
                       reauth_attempt|reauth_fail|reauth_reset|cooldown_set
        detail  str    short human note (NO tokens/accounts/credentials)
        source  str    who wrote it (module / pod name)
    """

    @abc.abstractmethod
    def append(self, env: str, event: str, detail: str = "", source: str = "") -> None: ...

    @abc.abstractmethod
    def read(self, limit: Optional[int] = None) -> list: ...


class PositionSnapshotStore(abc.ABC):
    """Optional history of fetched positions (non-secret aggregate trace). SHAREABLE.

    Record schema::

        ts        float
        env       str
        positions list[dict]   (symbol/qty/cost/price/mval/date_acquired/account_last4)
    """

    @abc.abstractmethod
    def save(self, env: str, positions: list) -> None: ...

    @abc.abstractmethod
    def latest(self, env: str) -> Optional[dict]: ...


# ===========================================================================
# File adapters (today's behaviour — delegate to the proven core)
# ===========================================================================

class FileTokenStore(TokenStore):
    def load(self, env: str) -> Optional[dict]:
        return _pkg()._load_tokens(env)

    def load_any_date(self, env: str) -> Optional[dict]:
        return _pkg()._load_tokens_any_date(env)

    def save(self, tokens: dict, env: str) -> None:
        _pkg()._save_tokens(tokens, env)

    def delete(self) -> None:
        p = _pkg()
        if p.os.path.exists(p._TOKEN_PATH):
            p.os.remove(p._TOKEN_PATH)


class FileBrowserStateStore(BrowserStateStore):
    def exists(self) -> bool:
        p = _pkg()
        return p.os.path.exists(p._BROWSER_STATE_PATH)

    def load(self) -> Optional[dict]:
        p = _pkg()
        if not p.os.path.exists(p._BROWSER_STATE_PATH):
            return None
        with open(p._BROWSER_STATE_PATH, encoding="utf-8") as f:
            return json.load(f)

    def save(self, blob: dict) -> None:
        p = _pkg()
        os.makedirs(os.path.dirname(p._BROWSER_STATE_PATH), exist_ok=True)
        with open(p._BROWSER_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(blob, f, indent=2, ensure_ascii=False)


class FileReauthStateStore(ReauthStateStore):
    def load(self, env: str = "production") -> dict:
        return _pkg()._load_reauth_state(env)

    def save(self, state: dict, env: str = "production") -> None:
        _pkg()._save_reauth_state(state, env)

    def reset(self, env: str = "production") -> None:
        _pkg().reset_reauth_circuit_breaker(env)


class FileLockProvider(LockProvider):
    """Minimal O_EXCL lockfile with a TTL, beside the other Data/ state files.

    This is the single-box adapter; the auth core still uses its own
    ``TokenRenewer`` two-level lock for the browser/renew single-flight. This
    port exists so a DB row-lease adapter can provide the SAME guarantee across
    pods without any call-site change.
    """

    def _path(self, name: str) -> str:
        return os.path.join(_pkg()._DIR, "Data", f"etrade_{name}.lock")

    def acquire(self, name: str, ttl: float = 30.0) -> bool:
        path = self._path(name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # Break a stale lock older than ttl.
        try:
            if os.path.exists(path) and (time.time() - os.path.getmtime(path)) > ttl:
                os.remove(path)
        except OSError:
            pass
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            return False

    def release(self, name: str) -> None:
        try:
            os.remove(self._path(name))
        except OSError:
            pass


class FileAuthEventLog(AuthEventLog):
    def _path(self) -> str:
        return os.path.join(_pkg()._DIR, "Data", "etrade_auth_events.jsonl")

    def append(self, env: str, event: str, detail: str = "", source: str = "") -> None:
        rec = {"ts": time.time(), "env": env, "event": event,
               "detail": detail, "source": source}
        path = self._path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
        except OSError:
            pass  # audit logging must never break the auth path

    def read(self, limit: Optional[int] = None) -> list:
        path = self._path()
        if not os.path.exists(path):
            return []
        out = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue
        return out[-limit:] if limit else out


class FilePositionSnapshotStore(PositionSnapshotStore):
    def _path(self) -> str:
        return os.path.join(_pkg()._DIR, "Data", "etrade_position_snapshots.jsonl")

    def save(self, env: str, positions: list) -> None:
        rec = {"ts": time.time(), "env": env, "positions": positions}
        path = self._path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, default=str) + "\n")
        except OSError:
            pass

    def latest(self, env: str) -> Optional[dict]:
        path = self._path()
        if not os.path.exists(path):
            return None
        found = None
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("env") == env:
                    found = rec
        return found


# ===========================================================================
# Store bundle + factory
# ===========================================================================

class EtradeStore:
    """Bundle of the six state ports, selected together for one backend."""

    def __init__(self, tokens: TokenStore, browser_state: BrowserStateStore,
                 reauth: ReauthStateStore, locks: LockProvider,
                 events: AuthEventLog, positions: PositionSnapshotStore,
                 backend: str = "file"):
        self.tokens = tokens
        self.browser_state = browser_state
        self.reauth = reauth
        self.locks = locks
        self.events = events
        self.positions = positions
        self.backend = backend


def _file_store() -> EtradeStore:
    return EtradeStore(
        tokens=FileTokenStore(),
        browser_state=FileBrowserStateStore(),
        reauth=FileReauthStateStore(),
        locks=FileLockProvider(),
        events=FileAuthEventLog(),
        positions=FilePositionSnapshotStore(),
        backend="file",
    )


def make_etrade_store(config=None) -> EtradeStore:
    """Select the persistence backend.

    ``DATABASE_URL`` empty / unset  -> file backend (today's behaviour).
    ``DATABASE_URL`` set            -> DB backend (aether.etrade.store_db).

    ``config`` may be any object exposing ``database_url`` (defaults to the global
    ``CFG``); env var ``DATABASE_URL`` always wins, matching CFG's env-over-json rule.
    """
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        if config is None:
            try:
                from aether.config import CFG
                config = CFG
            except Exception:
                config = None
        db_url = getattr(config, "database_url", None) if config is not None else None
    if not db_url:
        return _file_store()
    # DB backend requested — kept isolated so importing it (and SQLAlchemy) is
    # only paid for when actually selected.
    from aether.etrade.store_db import make_db_store
    return make_db_store(db_url)
