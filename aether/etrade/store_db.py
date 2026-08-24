"""Database adapter seam for the E*TRADE persistence ports (FUTURE backend).

This is the drop-in target for ``make_etrade_store`` when ``DATABASE_URL`` is set.
It is a **stub**: the interfaces are declared so call sites compile and the factory
contract holds, but the actual SQLAlchemy wiring + Alembic migrations land in the
Phase 5 DB-migration step. Every method raises ``NotImplementedError`` with a clear
pointer rather than silently returning wrong data.

SECURITY CONTRACT (enforced when this is implemented):
  * ``tokens`` and ``browser_state`` are SECRET — they must NOT be written to a
    plain DB table. In a DB deployment they stay in a k8s Secret / envelope-
    encrypted blob; only the NON-SECRET store below is backed by a DB table:
        etrade_reauth_state      (circuit-breaker counters)
"""
from __future__ import annotations

from aether.etrade.store import (
    EtradeStore, TokenStore, BrowserStateStore, ReauthStateStore,
)

_TODO = (
    "E*TRADE DB backend is not implemented yet (Phase 5: Alembic migrations + "
    "file->DB backfill). Unset DATABASE_URL to use the file backend, or implement "
    "aether/etrade/store_db.py."
)


class _DbTokenStore(TokenStore):
    def load(self, env): raise NotImplementedError(_TODO)
    def load_any_date(self, env): raise NotImplementedError(_TODO)
    def save(self, tokens, env): raise NotImplementedError(_TODO)
    def delete(self): raise NotImplementedError(_TODO)


class _DbBrowserStateStore(BrowserStateStore):
    def exists(self): raise NotImplementedError(_TODO)
    def load(self): raise NotImplementedError(_TODO)
    def save(self, blob): raise NotImplementedError(_TODO)


class _DbReauthStateStore(ReauthStateStore):
    def load(self, env="production"): raise NotImplementedError(_TODO)
    def save(self, state, env="production"): raise NotImplementedError(_TODO)
    def reset(self, env="production"): raise NotImplementedError(_TODO)


def make_db_store(database_url: str) -> EtradeStore:
    """Return a DB-backed store bundle. STUB — see module docstring / Phase 5."""
    return EtradeStore(
        tokens=_DbTokenStore(),
        browser_state=_DbBrowserStateStore(),
        reauth=_DbReauthStateStore(),
        backend="db",
    )
