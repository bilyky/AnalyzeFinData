"""Single source of truth for AETHER's canonical Data/ directory.

Both aether.etrade (auth-state files: token, browser state, breaker, locks) and
aether.trash (the soft-delete garbage can) resolve their Data/ location HERE, so a set
AETHER_DATA_DIR pins the token AND its trash to the SAME directory. That closes the
split-brain where a rejected token, living under an overridden Data/, was moved to a
DIFFERENT checkout's Data/.trash — or, across a filesystem boundary, failed to move at
all (os.replace raises cross-device) and was silently left live.

data_dir() reads the environment on EVERY call (not once at import) so importlib.reload
of a consumer under a patched AETHER_DATA_DIR re-resolves correctly — see
tests/test_etrade_data_dir.py, which reloads aether.etrade under a mocked environment.
"""
import os

_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def data_dir() -> str:
    """Absolute path to AETHER's Data/ dir: $AETHER_DATA_DIR if set, else <checkout>/Data.

    The default (<checkout>/Data) is checkout-relative and unchanged from legacy behavior;
    set AETHER_DATA_DIR to an absolute path to pin every Data/ file to one shared location
    regardless of which checkout (or git worktree) runs the code.
    """
    return os.environ.get("AETHER_DATA_DIR") or os.path.join(_DIR, "Data")


def cache_dir() -> str:
    """Root of the large, re-fetchable market-data caches: $AETHER_CACHE_DIR, else <checkout>/Data.

    Deliberately separate from AETHER_DATA_DIR. That one also moves the E*TRADE auth state
    (token, browser state, reauth lock) and the trash, so pointing a worktree at the real
    Data/ with it would make the worktree read and write the PRODUCTION token. Setting
    AETHER_CACHE_DIR moves ONLY Data/Symbol and Data/Symbol_full, which is what a worktree
    backtest or study needs. Use it instead of a directory junction: on 2026-10-06 removing a
    worktree whose cache dirs were junctions into the main checkout deleted the real caches.

    Unset, this is <checkout>/Data, exactly the legacy location (it does NOT follow
    AETHER_DATA_DIR), so behavior is unchanged. Modules read it once at import, so set the
    variable before the process starts.
    """
    return os.environ.get("AETHER_CACHE_DIR") or os.path.join(_DIR, "Data")


def symbol_dir() -> str:
    """Per-symbol daily Chaikin snapshots (Data/Symbol/<SYM>/<SYM>_<date>.json)."""
    return os.path.join(cache_dir(), "Symbol")


def ohlcv_dir() -> str:
    """OHLCV history cache (Data/Symbol_full/<SYM>_daily.json)."""
    return os.path.join(cache_dir(), "Symbol_full")
