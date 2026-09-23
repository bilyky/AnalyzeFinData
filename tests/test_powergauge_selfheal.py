"""
Regression tests for powergauge._login_via_browser's self-heal on login failure.

The durable Chaikin credential is the persistent Chrome profile's cf_clearance
cookie (~355-day life). A HEADLESS login is *expected* to fail Turnstile and can
NOT re-solve a cold challenge, so a headless failure must keep the profile intact.

A HEADED failure is USUALLY a transient Turnstile 600010 / network blip, not a
poisoned profile — and wiping the profile throws away a good ~355-day credential
and makes the next cold challenge HARDER, not easier. So a headed failure now
PRESERVES the profile by default; the destructive back-up-then-clear self-heal for
a genuinely poisoned profile is opt-in via CHAIKIN_PROFILE_RESET=1.

  python -m unittest tests.test_powergauge_selfheal -v
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import powergauge as pg


class TestLoginSelfHealScope(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="pg_selfheal_")
        self.profile_dir = os.path.join(self._tmp, "chaikin_chrome_profile")
        os.makedirs(self.profile_dir, exist_ok=True)
        # Sentinel that stands in for the durable cf_clearance-bearing profile.
        with open(os.path.join(self.profile_dir, "cookies.sqlite"), "w") as f:
            f.write("cf_clearance")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _run_login(self, headless, reset_env=None):
        # Force the except path: sync_playwright() raises when entered as a context
        # manager, deterministically, with no real browser or network.
        failing_sp = mock.MagicMock()
        failing_sp.return_value.__enter__.side_effect = RuntimeError("Turnstile error 600010")
        # Pin CHAIKIN_PROFILE_RESET deterministically so ambient env can't flip the
        # branch under test: reset_env=None ⇒ key absent (default preserve path),
        # reset_env="1" ⇒ opt-in back-up-then-clear self-heal.
        env_patch = {} if reset_env is None else {"CHAIKIN_PROFILE_RESET": reset_env}
        with mock.patch.dict(os.environ, env_patch, clear=False), \
             mock.patch.object(pg, "_CHAIKIN_PROFILE_DIR", self.profile_dir), \
             mock.patch.object(pg, "sync_playwright", failing_sp), \
             mock.patch.object(pg, "_resolve_proxy", return_value=""):
            if reset_env is None:
                os.environ.pop("CHAIKIN_PROFILE_RESET", None)
            # The original failure is always re-raised, whichever branch runs.
            with self.assertRaises(RuntimeError):
                pg._login_via_browser(headless=headless)

    def test_headless_failure_keeps_profile(self):
        # Headless can't re-solve a cold Turnstile — wiping the profile would throw
        # away a good credential and make things strictly worse, so it must survive.
        self._run_login(headless=True)
        self.assertTrue(
            os.path.isdir(self.profile_dir),
            "headless login failure must NOT delete the persistent Chrome profile",
        )
        backup_root = os.path.join(os.path.dirname(self.profile_dir), "Backup")
        self.assertFalse(
            os.path.isdir(backup_root),
            "headless failure must not back up/clear the profile at all",
        )

    def test_headed_failure_preserves_profile_by_default(self):
        # A headed failure is usually a transient 600010/network blip, so by default
        # (no CHAIKIN_PROFILE_RESET) the durable cf_clearance-bearing profile must
        # survive — wiping it would make the next cold Turnstile challenge harder.
        self._run_login(headless=False, reset_env=None)
        self.assertTrue(
            os.path.isdir(self.profile_dir),
            "headed login failure must PRESERVE the profile by default",
        )
        backup_root = os.path.join(os.path.dirname(self.profile_dir), "Backup")
        self.assertFalse(
            os.path.isdir(backup_root),
            "default headed failure must not back up/clear the profile",
        )

    def test_headed_failure_clears_profile_when_reset_env_set(self):
        # Opt-in self-heal for a genuinely poisoned profile: CHAIKIN_PROFILE_RESET=1
        # backs the profile up first (Mandatory Backup Policy) then clears it.
        self._run_login(headless=False, reset_env="1")
        self.assertFalse(
            os.path.isdir(self.profile_dir),
            "CHAIKIN_PROFILE_RESET=1 headed failure should clear the poisoned profile",
        )
        backup_root = os.path.join(os.path.dirname(self.profile_dir), "Backup")
        backups = os.listdir(backup_root) if os.path.isdir(backup_root) else []
        self.assertTrue(
            any(b.startswith("chaikin_profile_backup_") for b in backups),
            "opt-in self-heal must back the profile up before clearing it",
        )


if __name__ == "__main__":
    unittest.main()
