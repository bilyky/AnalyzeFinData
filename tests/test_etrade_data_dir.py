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
import unittest
from unittest import mock

import aether.etrade as etrade


class TestCanonicalAuthDataDir(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
