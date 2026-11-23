"""Shared Chaikin session for PROD and dev (plan T0.7), stored in the same Postgres as the E*TRADE token.

Before this, each machine kept its own ``Data/session.json``. Dev had none, so a dev run that needed Chaikin
opened a browser login (or failed in a worktree). On 2026-11-22 one renewed session worked on both machines at
once (stage 0: dev used PROD's session, PROD's probe still said valid), so one shared copy is safe.

How it works:
  * **Explicit opt-in:** ``AETHER_CHAIKIN_STORE=db``. Anything else (unset, ``file``) keeps today's behaviour,
    file only. A database URL alone never turns it on (the #144 lesson).
  * **The file stays.** Every save writes ``Data/session.json`` first; with the opt-in it also writes the row.
  * **Newest wins on load.** The row's ``saved_at`` is compared with the file's modified time, so a stale row
    (written before a database outage) never overrides a fresher renewal saved to the file.
  * **Encrypted.** The row holds a Fernet blob; the key is derived (HKDF) from the E*TRADE production consumer
    secret, which both machines have and the database does not. No secret -> the store is unavailable.
  * **Fallback.** Any database problem falls back to the file with one warning per process.

One writer: :func:`save` is the only code that writes the row; powergauge's ``_save_session_to_file`` (used by
the renewal paths) is its only caller.

Command line (read-only unless --seed):
    python -m aether.chaikin_store --status     # which copy is newer, their ages; never prints secrets
    python -m aether.chaikin_store --seed       # copy this machine's session.json into the row (PROD, once)
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import sys
import time

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import Column, Float, MetaData, String, Table, Text, create_engine, insert, select, update
from sqlalchemy.exc import SQLAlchemyError

from aether import paths
from aether.config import CFG
from aether.logger import get_logger

_log = get_logger("chaikin_store")

ENV_VAR = "AETHER_CHAIKIN_STORE"
ROW = "default"
CONNECT_TIMEOUT_S = 5
_HKDF_SALT = b"aether-chaikin-store-v1"

_META = MetaData()
SESSIONS = Table(
    "chaikin_session", _META,
    Column("name", String(32), primary_key=True),
    Column("payload", Text, nullable=False),
    Column("saved_at", Float, nullable=False),
    Column("updated_by", String(255), nullable=False),
)
_ENGINES: dict = {}
_WARNED: set = set()
BREAKER_S = 600          # after a database failure, use the file only for 10 minutes in this process
READ_TTL_S = 60          # reuse a good row read for a minute
_STATE: dict = {"down_until": 0.0, "cached": None}


class StoreUnavailable(Exception):
    """The shared row can't be used (off, no URL, no key, database error, bad blob)."""


def enabled() -> bool:
    return (os.environ.get(ENV_VAR) or "file").strip().lower() == "db"


def _config(config=None):
    return config if config is not None else CFG


def _url_and_key(config=None) -> tuple[str, str]:
    cfg = _config(config)
    url = os.environ.get("DATABASE_URL") or (getattr(cfg, "database_url", "") if cfg is not None else "")
    key = (getattr(cfg, "etrade_production_secret", "") or "") if cfg is not None else ""
    if not url:
        raise StoreUnavailable(f"{ENV_VAR}=db but no DATABASE_URL / database.url is configured")
    if not key:
        raise StoreUnavailable("no E*TRADE production secret to derive the encryption key from")
    return url, key


def _fernet(key_secret: str) -> Fernet:
    raw = HKDF(algorithm=hashes.SHA256(), length=32, salt=_HKDF_SALT, info=b"chaikin session").derive(
        key_secret.encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(raw))


def _engine(url: str):
    eng = _ENGINES.get(url)
    if eng is None:
        args = {"connect_timeout": CONNECT_TIMEOUT_S} if url.startswith("postgresql") else {}
        try:
            eng = create_engine(url, pool_pre_ping=True, connect_args=args)
            _META.create_all(eng)
        except SQLAlchemyError as e:
            raise StoreUnavailable(f"Chaikin shared store unreachable: {type(e).__name__}") from e
        _ENGINES[url] = eng
    return eng


def db_read(config=None) -> tuple[dict, float] | None:
    """(session, saved_at) from the row, None when there is no row. Raises StoreUnavailable."""
    url, key = _url_and_key(config)
    try:
        with _engine(url).begin() as conn:
            row = conn.execute(select(SESSIONS.c.payload, SESSIONS.c.saved_at).where(SESSIONS.c.name == ROW)).first()
    except SQLAlchemyError as e:
        raise StoreUnavailable(f"Chaikin shared store error: {type(e).__name__}") from e
    if row is None:
        return None
    try:
        data = json.loads(_fernet(key).decrypt(row[0].encode("ascii")).decode("utf-8"))
    except (InvalidToken, ValueError) as e:
        raise StoreUnavailable(f"Chaikin shared row can't be decrypted: {type(e).__name__}") from e
    return data, float(row[1])


def db_write(session: dict, config=None, now: float | None = None) -> None:
    url, key = _url_and_key(config)
    blob = _fernet(key).encrypt(json.dumps(session).encode("utf-8")).decode("ascii")
    vals = {"payload": blob, "saved_at": time.time() if now is None else now,
            "updated_by": f"{socket.gethostname()}:{os.getpid()}"}
    try:
        with _engine(url).begin() as conn:
            if conn.execute(update(SESSIONS).where(SESSIONS.c.name == ROW).values(**vals)).rowcount == 0:
                conn.execute(insert(SESSIONS).values(name=ROW, **vals))
    except SQLAlchemyError as e:
        raise StoreUnavailable(f"Chaikin shared store error: {type(e).__name__}") from e
    _STATE["cached"] = None                    # this process's cached read is now stale


def _warn_once(what: str, err: Exception) -> None:
    if what not in _WARNED:
        _WARNED.add(what)
        _log.warning(f"Chaikin shared store unavailable ({what}: {err}); using this machine's session.json")


def _down() -> bool:
    """True while the breaker is open: a failure in this process skips the database for BREAKER_S."""
    return time.time() < _STATE["down_until"]


def _trip(what: str, err: Exception) -> None:
    _STATE["down_until"] = time.time() + BREAKER_S
    _STATE["cached"] = None
    _warn_once(what, err)


def _read_cached(config=None):
    """db_read at most once per READ_TTL_S (ensure_valid_session runs per symbol, ~533 a day)."""
    now = time.time()
    hit = _STATE["cached"]
    if hit is not None and now - hit[0] < READ_TTL_S:
        return hit[1]
    got = db_read(config)
    _STATE["cached"] = (now, got)
    return got


def load(file_loader, file_path: str, config=None) -> dict:
    """The newer of the shared row and this machine's file; the file alone when the store is off or down.

    Review of #313: with the database unreachable, each connection attempt waits CONNECT_TIMEOUT_S, and this runs
    for every symbol fetched. A failure opens a breaker (no database for BREAKER_S), and a good read is reused for
    READ_TTL_S, so an outage costs one timeout per BREAKER_S instead of one per symbol."""
    local = file_loader()
    if not enabled() or _down():
        return local
    try:
        got = _read_cached(config)
    except StoreUnavailable as e:
        _trip("load", e)
        return local
    if got is None:
        return local
    shared, saved_at = got
    try:
        file_time = os.path.getmtime(file_path) if local else 0.0
    except OSError:
        file_time = 0.0
    return local if (local and file_time > saved_at) else shared


def save(session: dict, file_saver, config=None) -> None:
    """Write this machine's file, then (opt-in) the shared row. A database failure never blocks the file."""
    file_saver(session)
    _STATE["cached"] = None                    # the next load reads the row again
    if not enabled() or _down():
        return
    try:
        db_write(session, config)
    except StoreUnavailable as e:
        _trip("save", e)


def status(file_path: str, config=None) -> dict:
    """What --status prints: ages and which copy load() would use. No session values."""
    out = {"enabled": enabled(), "file": None, "row": None, "load_uses": "file"}
    try:
        out["file"] = {"age_h": round((time.time() - os.path.getmtime(file_path)) / 3600, 2)}
    except OSError:
        pass
    if not out["enabled"]:
        return out
    try:
        got = db_read(config)
    except StoreUnavailable as e:
        out["row"] = {"error": str(e)}
        return out
    if got:
        out["row"] = {"age_h": round((time.time() - got[1]) / 3600, 2)}
        f_age = out["file"]["age_h"] if out["file"] else None
        out["load_uses"] = "file" if (f_age is not None and f_age < out["row"]["age_h"]) else "row"
    return out


def session_file() -> str:
    """This machine's session file: the same path powergauge.SESSION_FILE uses."""
    return os.path.join(paths.data_dir(), "session.json")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Shared Chaikin session (plan T0.7)")
    ap.add_argument("--status", action="store_true", help="show which copy is newer (read-only)")
    ap.add_argument("--seed", action="store_true", help="copy this machine's session.json into the shared row")
    a = ap.parse_args(argv)
    if a.seed:
        if not enabled():
            sys.stderr.write(f"set {ENV_VAR}=db first\n")
            return 2
        try:
            with open(session_file(), encoding="utf-8") as f:
                session = json.load(f)
        except (OSError, ValueError):
            session = {}
        if not session.get("jsessionid"):
            sys.stderr.write("no usable session.json on this machine\n")
            return 2
        db_write(session)
        sys.stdout.write("seeded the shared row from session.json\n")
        return 0
    sys.stdout.write(json.dumps(status(session_file()), indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
