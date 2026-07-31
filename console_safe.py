"""Windows CP1252 console fallback (bug-fix workaround, not a feature).

On a legacy Windows console, printing a non-ASCII glyph (emoji, the bullish
arrow, ...) raises UnicodeEncodeError and can crash a headless Task Scheduler
run. install() reconfigures stdout/stderr to UTF-8 and wraps them so a line that
still can't be encoded degrades to a '?' placeholder instead of crashing.

Import once, early, from an entry point:

    import console_safe
    console_safe.install()

Scope is the process that calls install(); it is idempotent and a no-op off
Windows. This REDUCES but does not "completely prevent" console encoding
crashes — a child ``python`` launched without ``PYTHONIOENCODING=utf-8`` gets a
fresh, unwrapped stream. See AETHER_REFERENCE.md "Windows CP1252 Console
Fallback".
"""
import sys


class SafeStreamWrapper:
    """Wrap a text stream so a write that can't be encoded falls back to a lossy
    ``errors='replace'`` re-encode instead of raising UnicodeEncodeError."""

    def __init__(self, stream):
        self._stream = stream

    def write(self, s):
        try:
            return self._stream.write(s)
        except UnicodeEncodeError:
            encoding = getattr(self._stream, "encoding", "cp1252") or "cp1252"
            return self._stream.write(s.encode(encoding, errors="replace").decode(encoding))

    def __getattr__(self, name):
        return getattr(self._stream, name)


def install():
    """Idempotently reconfigure and wrap stdout/stderr on Windows; no-op else."""
    if sys.platform != "win32":
        return
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if stream is None:
            continue   # pythonw.exe / fully headless: no console stream to wrap
        if isinstance(stream, SafeStreamWrapper):
            continue   # already installed — don't double-wrap
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass       # some streams (pipes, captured buffers) can't reconfigure
        setattr(sys, name, SafeStreamWrapper(stream))
