"""Database adapter for the E*TRADE persistence ports: one shared E*TRADE login for several machines.

Selected by ``make_etrade_store`` with the explicit ``AETHER_ETRADE_STORE=db`` opt-in. Both machines
(PROD and dev) point ``database.url`` at the same Postgres, so a token one machine mints is the token
the other loads. A new login no longer kills the other machine's session (PROD 2026-11-11/12:
dev's dashboard logins and PROD's rejected tokens alternated four times).

Tables (created on first use, portable SQL; the tests run them on SQLite):
    etrade_tokens        env | payload (encrypted) | fingerprint | saved_at | issued_date_et | updated_by
    etrade_reauth_state  env | consecutive_failures | last_attempt | cooldown_until
    etrade_locks         name | owner | expires_at          (row lease, TTL-reclaimed)

SECURITY CONTRACT:
  * The token row holds an **encrypted blob** only (Fernet; the key is derived with HKDF from
    the E*TRADE consumer secret, which every machine has and the database does not). A dump of
    the table alone does not reveal the OAuth token or secret. ``fingerprint`` is a SHA-256 of the
    OAuth token, used only to delete exactly the token a caller saw rejected.
  * ``browser_state`` (Playwright trusted-device cookies) is NOT shared: it stays in the
    per-machine file adapter (``FileBrowserStateStore``).

Every database failure raises :class:`aether.etrade.store.StoreUnavailable`, which the fallback
wrapper in ``aether.etrade.store`` turns into a local-file operation plus one warning.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import time
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import Column, Float, Integer, MetaData, String, Table, Text, create_engine, delete, insert, select, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from aether.etrade.store import (
    EtradeStore, TokenStore, ReauthStateStore, LockProvider, LockHandle, FileBrowserStateStore,
    StoreUnavailable, _DEFAULT_LOCK_TTL, _pkg,
)

_META = MetaData()
TOKENS = Table(
    "etrade_tokens", _META,
    Column("env", String(16), primary_key=True),
    Column("payload", Text, nullable=False),
    Column("fingerprint", String(64), nullable=False),
    Column("saved_at", Float, nullable=False),
    Column("issued_date_et", String(10), nullable=False),
    Column("updated_by", String(255), nullable=False),
)
REAUTH = Table(
    "etrade_reauth_state", _META,
    Column("env", String(16), primary_key=True),
    Column("consecutive_failures", Integer, nullable=False),
    Column("last_attempt", Float, nullable=False),
    Column("cooldown_until", Float, nullable=False),
)
LOCKS = Table(
    "etrade_locks", _META,
    Column("name", String(128), primary_key=True),
    Column("owner", String(255), nullable=False),
    Column("expires_at", Float, nullable=False),
)

# Fail fast when the database is unreachable, so the fallback starts in seconds, not minutes.
CONNECT_TIMEOUT_S = 5
_HKDF_SALT = b"aether-etrade-store-v1"
_ENGINES: dict = {}


def _owner() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def _engine(url: str):
    """One engine per URL, created lazily (constructing a store never connects); tables on first use."""
    eng = _ENGINES.get(url)
    if eng is None:
        args = {"connect_timeout": CONNECT_TIMEOUT_S} if url.startswith("postgresql") else {}
        try:
            eng = create_engine(url, pool_pre_ping=True, connect_args=args)
            _META.create_all(eng)
        except SQLAlchemyError as e:
            raise StoreUnavailable(f"E*TRADE shared store unreachable: {type(e).__name__}") from e
        _ENGINES[url] = eng
    return eng


def _fernet(key_secret: str) -> Fernet:
    raw = HKDF(algorithm=hashes.SHA256(), length=32, salt=_HKDF_SALT, info=b"etrade tokens").derive(
        key_secret.encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(raw))


def fingerprint(oauth_token: str) -> str:
    return hashlib.sha256((oauth_token or "").encode("utf-8")).hexdigest()


class _Db:
    """Shared connection plumbing: every SQLAlchemy error becomes StoreUnavailable."""

    def __init__(self, url: str):
        self._url = url

    def run(self, fn):
        try:
            with _engine(self._url).begin() as conn:
                return fn(conn)
        except StoreUnavailable:
            raise
        except SQLAlchemyError as e:
            raise StoreUnavailable(f"E*TRADE shared store error: {type(e).__name__}") from e


class DbTokenStore(TokenStore):
    def __init__(self, db: _Db, key_secret: str):
        if not key_secret:
            raise ValueError("The shared E*TRADE store needs the production consumer secret to encrypt tokens.")
        self._db = db
        self._fernet = _fernet(key_secret)

    def _row(self, env: str) -> Optional[dict]:
        row = self._db.run(lambda c: c.execute(select(TOKENS).where(TOKENS.c.env == env)).mappings().first())
        if row is None:
            return None
        try:
            tokens = json.loads(self._fernet.decrypt(row["payload"].encode("ascii")))
        except (InvalidToken, ValueError) as e:
            # A different consumer secret (or a corrupt row): unusable, but not a store outage.
            _pkg()._log.warning(f"E*TRADE shared store: the {env} token can't be decrypted here ({type(e).__name__}).")
            return None
        return tokens if tokens.get("env") == env else None

    def load(self, env: str) -> Optional[dict]:
        p = _pkg()
        tokens = self._row(env)
        if not tokens:
            return None
        if tokens.get("issued_date_et") != p._et_today():
            p._log.info("Shared E*TRADE token is from a previous trading day — re-authenticating...")
            return None
        age_min = max(0.0, p.time.time() - tokens.get("saved_at", 0)) / 60
        p._log.info(f"Shared E*TRADE token found ({age_min:.0f} min old, issued today ET).")
        return tokens

    def load_any_date(self, env: str) -> Optional[dict]:
        return self._row(env)

    def save(self, tokens: dict, env: str) -> None:
        p = _pkg()
        tokens["env"] = env
        tokens["saved_at"] = p.time.time()
        tokens["issued_date_et"] = p._et_today()
        row = {"payload": self._fernet.encrypt(json.dumps(tokens).encode("utf-8")).decode("ascii"),
               "fingerprint": fingerprint(tokens.get("oauth_token", "")),
               "saved_at": tokens["saved_at"], "issued_date_et": tokens["issued_date_et"], "updated_by": _owner()}

        def upsert(c):
            if c.execute(update(TOKENS).where(TOKENS.c.env == env).values(**row)).rowcount == 0:
                c.execute(insert(TOKENS).values(env=env, **row))
        self._db.run(upsert)
        p._log.info(f"E*TRADE {env} token saved to the shared store ({tokens['issued_date_et']}).")

    def delete(self, env: Optional[str] = None, if_token: Optional[str] = None) -> None:
        """Delete the shared token. With ``if_token``, only while the shared row still holds that
        token: a machine that saw its token rejected must not delete the other machine's new one."""
        def drop(c):
            q = delete(TOKENS)
            if env:
                q = q.where(TOKENS.c.env == env)
            if if_token:
                q = q.where(TOKENS.c.fingerprint == fingerprint(if_token))
            return c.execute(q).rowcount
        self._db.run(drop)


class DbReauthStateStore(ReauthStateStore):
    def __init__(self, db: _Db):
        self._db = db

    def load(self, env: str = "production") -> dict:
        row = self._db.run(lambda c: c.execute(select(REAUTH).where(REAUTH.c.env == env)).mappings().first())
        if row is None:
            return {"consecutive_failures": 0, "last_attempt": 0.0, "cooldown_until": 0.0}
        return {"consecutive_failures": int(row["consecutive_failures"]),
                "last_attempt": float(row["last_attempt"]), "cooldown_until": float(row["cooldown_until"])}

    def save(self, state: dict, env: str = "production") -> None:
        vals = {"consecutive_failures": int(state.get("consecutive_failures", 0)),
                "last_attempt": float(state.get("last_attempt", 0.0)),
                "cooldown_until": float(state.get("cooldown_until", 0.0))}

        def upsert(c):
            if c.execute(update(REAUTH).where(REAUTH.c.env == env).values(**vals)).rowcount == 0:
                c.execute(insert(REAUTH).values(env=env, **vals))
        self._db.run(upsert)

    def reset(self, env: str = "production") -> None:
        self.save({"consecutive_failures": 0, "last_attempt": 0.0, "cooldown_until": 0.0}, env)


def _db_epoch(conn) -> float:
    """The database's clock in epoch seconds. Leases are written and checked against it, so two
    machines whose clocks differ can't take over each other's live lease (#268 review)."""
    if conn.dialect.name == "postgresql":
        return float(conn.execute(text("SELECT EXTRACT(EPOCH FROM clock_timestamp())")).scalar())
    if conn.dialect.name == "sqlite":
        return float(conn.execute(text("SELECT (julianday('now') - 2440587.5) * 86400.0")).scalar())
    return time.time()


class DbLockProvider(LockProvider):
    """Row-lease lock: one row per lock name, reclaimed once its lease (``ttl``) has expired. A
    crashed holder therefore blocks others for at most ``ttl`` seconds, as with the file lock."""

    def __init__(self, db: _Db):
        self._db = db

    def acquire(self, name: str, ttl: int = _DEFAULT_LOCK_TTL) -> Optional[LockHandle]:
        owner = f"{_owner()}:{time.time():.6f}"   # unique per attempt; not used as a clock

        def take(c):
            now = _db_epoch(c)
            try:
                with c.begin_nested():
                    c.execute(insert(LOCKS).values(name=name, owner=owner, expires_at=now + ttl))
                return True
            except IntegrityError:
                pass
            # Held: reclaim only an expired lease, in the same transaction.
            if c.execute(delete(LOCKS).where(LOCKS.c.name == name, LOCKS.c.expires_at < now)).rowcount:
                c.execute(insert(LOCKS).values(name=name, owner=owner, expires_at=now + ttl))
                return True
            return False
        if not self._db.run(take):
            return None
        return LockHandle(name=name, path=None, fd=None, owner=owner)

    def release(self, handle: Optional[LockHandle]) -> None:
        if handle is None:
            return
        self._db.run(lambda c: c.execute(delete(LOCKS).where(LOCKS.c.name == handle.name,
                                                              LOCKS.c.owner == handle.owner)))

    def is_held(self, name: str) -> bool:
        return bool(self._db.run(lambda c: c.execute(
            select(LOCKS.c.name).where(LOCKS.c.name == name, LOCKS.c.expires_at >= _db_epoch(c))).first()))


def make_db_store(database_url: str, key_secret: str = "") -> EtradeStore:
    """The shared-database bundle (no connection is made here). Browser state stays per machine."""
    db = _Db(database_url)
    return EtradeStore(
        tokens=DbTokenStore(db, key_secret) if key_secret else _NoKeyTokenStore(),
        browser_state=FileBrowserStateStore(),
        reauth=DbReauthStateStore(db),
        lock=DbLockProvider(db),
        backend="db",
    )


class _NoKeyTokenStore(TokenStore):
    """No consumer secret to derive the key from: the shared token can't be used on this machine."""

    def _no(self, *_a, **_k):
        raise StoreUnavailable("E*TRADE shared store: no production consumer secret configured to decrypt tokens.")

    load = load_any_date = save = delete = _no
