"""Canonical auth-state directory for E*TRADE (aether.etrade._DATA_DIR).

Regression anchor for the 2026-08-19 "agent couldn't locate the token file" incident:
the token path is CHECKOUT-RELATIVE (`<checkout>/Data/etrade_tokens.json`), so a human
re-auth launched from a git worktree wrote the fresh token into that worktree's dead
Data/, where prod's scheduled tasks never look. `AETHER_DATA_DIR` pins every auth-state
file to one shared absolute location so re-auth from any checkout lands where prod reads.

The paths are resolved at import time from the env var, so each case reloads the module
under a patched environment and the teardown restores the default resolution.
"""
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import aether.etrade as etrade
import aether.trash as trash

_ORIG_ETRADE = sys.modules.get("aether.etrade")
_ORIG_TRASH = sys.modules.get("aether.trash")


def _reapply_hermetic_redirects(e):
    """Re-apply global test-auth redirects if we are in a hermetic run,
    preventing any reloaded module instance from exposing prod Data/.
    """
    if not os.getenv("AETHER_LIVE_TESTS"):
        tmp_dir = tempfile.gettempdir()
        e._TOKEN_PATH         = str(Path(tmp_dir) / "etrade_tokens_test.json")
        e._BROWSER_STATE_PATH = str(Path(tmp_dir) / "etrade_browser_state_test.json")
        e._REAUTH_STATE_PATH  = str(Path(tmp_dir) / "etrade_reauth_state_test.json")
        e._FAIL_STATE_PATH    = str(Path(tmp_dir) / "etrade_fail_state_test.json")
        
        # Block Playwright browser launch on the reloaded etrade module instance
        def _blocked_playwright(*_a, **_k):
            raise RuntimeError(
                "Blocked Playwright browser launch in tests (E*TRADE/Chaikin auth). "
                "Mock the browser path, or set AETHER_LIVE_TESTS=1."
            )
            
        e.sync_playwright = _blocked_playwright


class TestCanonicalAuthDataDir(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        # Once all canonical directory tests are finished, permanently restore the
        # global hermetic redirects so all subsequent tests in the discovery run are
        # completely shielded from prod Data/.
        _reapply_hermetic_redirects(etrade)
        # Re-synchronize sys.modules references to completely prevent the Reload Mock-Split!
        if _ORIG_ETRADE is not None:
            sys.modules["aether.etrade"] = _ORIG_ETRADE
        if _ORIG_TRASH is not None:
            sys.modules["aether.trash"] = _ORIG_TRASH

    def tearDown(self):
        # Restore the module to its default (env-unset) path resolution for other tests.
        env = dict(os.environ)
        env.pop("AETHER_DATA_DIR", None)
        with mock.patch.dict(os.environ, env, clear=True):
            importlib.reload(etrade)

    def _reload_with(self, **env):
        base = dict(os.environ)
        base.pop("AETHER_DATA_DIR", None)
        base.update(env)
        with mock.patch.dict(os.environ, base, clear=True):
            importlib.reload(etrade)
        return etrade

    def test_default_is_checkout_data_dir(self):
        # Env unset → unchanged legacy behavior: <checkout>/Data (prod-from-prod, tests).
        e = self._reload_with()
        self.assertEqual(e._DATA_DIR, os.path.join(e._DIR, "Data"))
        self.assertEqual(e._TOKEN_PATH, os.path.join(e._DIR, "Data", "etrade_tokens.json"))

    def test_override_pins_every_auth_state_file(self):
        # AETHER_DATA_DIR set → token, browser state, and breaker all land in the ONE
        # canonical dir regardless of which checkout's code is running.
        canonical = os.path.join(os.sep + "srv", "aether-prod", "Data")
        e = self._reload_with(AETHER_DATA_DIR=canonical)
        self.assertEqual(e._DATA_DIR, canonical)
        self.assertEqual(e._TOKEN_PATH, os.path.join(canonical, "etrade_tokens.json"))
        self.assertEqual(e._BROWSER_STATE_PATH, os.path.join(canonical, "etrade_browser_state.json"))
        self.assertEqual(e._REAUTH_STATE_PATH, os.path.join(canonical, "etrade_reauth_state.json"))
        self.assertEqual(e._FAIL_STATE_PATH, os.path.join(canonical, "etrade_fail_state.json"))

    def test_override_relocates_per_env_breaker_too(self):
        # The per-env (sandbox/test) breaker derives from the canonical prod path, so the
        # override moves every env's state file together — no env leaks into <checkout>/Data.
        canonical = os.path.join(os.sep + "srv", "aether-prod", "Data")
        e = self._reload_with(AETHER_DATA_DIR=canonical)
        self.assertEqual(e._reauth_state_path("production"), e._REAUTH_STATE_PATH)
        self.assertEqual(os.path.dirname(e._reauth_state_path("sandbox")), canonical)

    def test_trash_co_locates_with_the_same_override(self):
        # Single source of truth: etrade auth-state and the soft-delete trash resolve Data/
        # through the SAME aether.paths.data_dir(), so a rejected token (under the override)
        # is trashed INTO that override's .trash — same filesystem, not a different checkout's
        # dead Data/. Red-green anchor for the split-brain the hardcoded TRASH_DIR created.
        canonical = os.path.join(os.sep + "srv", "aether-prod", "Data")
        base = dict(os.environ)
        base.pop("AETHER_DATA_DIR", None)
        base["AETHER_DATA_DIR"] = canonical
        try:
            with mock.patch.dict(os.environ, base, clear=True):
                importlib.reload(etrade)
                importlib.reload(trash)
                self.assertEqual(trash.TRASH_DIR, os.path.join(canonical, ".trash"))
                # co-located with the token it soft-deletes (same dir ⇒ atomic os.replace)
                self.assertEqual(os.path.dirname(trash.TRASH_DIR), etrade._DATA_DIR)
        finally:
            env = dict(os.environ)
            env.pop("AETHER_DATA_DIR", None)
            with mock.patch.dict(os.environ, env, clear=True):
                importlib.reload(trash)   # etrade is restored by tearDown


if __name__ == "__main__":
    unittest.main()
