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
