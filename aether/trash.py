"""Project-wide soft-delete ("garbage can") with a retention policy.

The single deletion entry point for AETHER runtime code. Instead of ``os.remove`` /
``os.unlink`` / ``Path.unlink`` scattered around — any of which can irrecoverably
destroy a file on a transient error or a bad edge case (the 2026-08-18 token-wipe
class of bug) — call :func:`soft_delete`. By default the file is *moved* to
``Data/.trash/`` so it stays recoverable, and it is only physically removed later, by
:func:`purge_trash` (run by the watchdog), once it ages past the retention window.

Pass ``force=True`` for the deliberate cases where a hard, unrecoverable delete is what
you actually want — lock files, temp files, already-redundant backups being pruned.

``Data/`` is fully gitignored, so nothing in the trash is ever committed.
"""
import logging
import os
import time

_log = logging.getLogger("aether.trash")

_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRASH_DIR = os.path.join(_DIR, "Data", ".trash")
RETENTION_DAYS = 30   # ~1 month; watchdog.run_watchdog() calls purge_trash()


def soft_delete(path, reason="unspecified", force=False) -> str | None:
    """Delete a file the safe way.

    Default (``force=False``): *move* ``path`` into the retention trash so it stays
    recoverable, returning the trash path. ``force=True``: hard-delete it
    (``os.remove``), returning ``None``.

    Returns ``None`` when the file did not exist, when ``force=True``, or when a
    soft-delete move failed — in which case the original is left in place (never a
    silent hard delete on the fallback path). Accepts ``str`` or ``os.PathLike``.
    """
    path = os.fspath(path)
    if not os.path.exists(path):
        return None
    if force:
        try:
            os.remove(path)
        except OSError as e:
            _log.warning(f"  [Trash] force-delete failed for {path}: {e}")
        return None
    try:
        os.makedirs(TRASH_DIR, exist_ok=True)
        base = f"{time.strftime('%Y%m%dT%H%M%S', time.localtime())}.{reason}.{os.path.basename(path)}"
        dest = os.path.join(TRASH_DIR, base)
        n = 1
        while os.path.exists(dest):            # keep every discarded copy distinct
            dest = os.path.join(TRASH_DIR, f"{base}.{n}")
            n += 1
        os.replace(path, dest)                 # atomic within the same filesystem
        return dest
    except Exception as e:
        _log.warning(f"  [Trash] Could not soft-delete {path}: {e} (left in place)")
        return None


def purge_trash(retention_days: int = RETENTION_DAYS) -> int:
    """Physically delete trashed files older than the retention window.

    The one place files are truly removed. The watchdog calls this each cycle so the
    garbage can self-cleans. Returns the number of files purged.
    """
    if not os.path.isdir(TRASH_DIR):
        return 0
    cutoff = time.time() - retention_days * 86400
    purged = 0
    for name in os.listdir(TRASH_DIR):
        p = os.path.join(TRASH_DIR, name)
        try:
            if os.path.isfile(p) and os.path.getmtime(p) < cutoff:
                os.remove(p)
                purged += 1
        except OSError:
            pass
    return purged
