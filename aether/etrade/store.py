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
  the *non-secret* rows — circuit-breaker state. OAuth secrets and browser state
  stay in a k8s Secret / envelope-encrypted blob. The port split below marks
  each store SECRET or SHAREABLE.
* Adapters touch the package lazily (inside methods), never at import time, so
  ``aether.etrade.__init__`` can import this module from its own bottom without a
  circular-import hazard.
"""
from __future__ import annotations

import abc
import json
import os
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


# ===========================================================================
# Store bundle + factory
# ===========================================================================

class EtradeStore:
    """Bundle of the three state ports, selected together for one backend."""

    def __init__(self, tokens: TokenStore, browser_state: BrowserStateStore,
                 reauth: ReauthStateStore, backend: str = "file"):
        self.tokens = tokens
        self.browser_state = browser_state
        self.reauth = reauth
        self.backend = backend


def _file_store() -> EtradeStore:
    return EtradeStore(
        tokens=FileTokenStore(),
        browser_state=FileBrowserStateStore(),
        reauth=FileReauthStateStore(),
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
