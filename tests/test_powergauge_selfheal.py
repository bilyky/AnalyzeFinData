"""
Regression tests for powergauge._login_via_browser's self-heal on login failure.

The durable Chaikin credential is the persistent Chrome profile's cf_clearance
cookie (~355-day life). A HEADLESS login is *expected* to fail Turnstile and can
NOT re-solve a cold challenge, so a headless failure must keep the profile intact.
Only a HEADED failure (which can re-solve) may back up + clear the profile to
self-heal a poisoned cf_clearance/aws-waf cookie (the 600010 case).

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

    def _run_login(self, headless):
        # Force the except path: sync_playwright() raises when entered as a context
        # manager, deterministically, with no real browser or network.
        failing_sp = mock.MagicMock()
        failing_sp.return_value.__enter__.side_effect = RuntimeError("Turnstile error 600010")
        with mock.patch.object(pg, "_CHAIKIN_PROFILE_DIR", self.profile_dir), \
             mock.patch.object(pg, "sync_playwright", failing_sp), \
             mock.patch.object(pg, "_resolve_proxy", return_value=""):
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

    def test_headed_failure_backs_up_then_clears_profile(self):
        # Headed CAN re-solve, so self-heal is safe: back up first, then clear.
        self._run_login(headless=False)
        self.assertFalse(
            os.path.isdir(self.profile_dir),
            "headed login failure should clear the (poisoned) profile to self-heal",
        )
        backup_root = os.path.join(os.path.dirname(self.profile_dir), "Backup")
        backups = os.listdir(backup_root) if os.path.isdir(backup_root) else []
        self.assertTrue(
            any(b.startswith("chaikin_profile_backup_") for b in backups),
            "headed self-heal must back the profile up before clearing it",
        )


if __name__ == "__main__":
    unittest.main()
