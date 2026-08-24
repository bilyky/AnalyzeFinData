"""
Red-green test for the post-run data-sync warning banner in daily_task.

The bug this guards: the inline check compared watchdog.sync_data_folder()'s BOOL
return value against the string tuple ("FAILED", "SKIPPED_OFFLINE"). A bool is never
`in` that tuple, so a genuine sync failure (return False) produced NO warning banner and
NO "(Sync Failed)" subject — the whole feature was dead.

`_sync_warning()` encodes the real contract of sync_data_folder() (-> bool):
  - sync_ok is False  → failure banner + "(Sync Failed)" subject   (RED under the old code)
  - error is not None → exception banner + "(Sync Failed)" subject
  - sync_ok is True   → no banner, normal subject                  (success / offline-bypass)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import daily_task


class TestSyncWarning(unittest.TestCase):
    TODAY = "2026-08-18"

    def test_false_sync_result_raises_failure_banner(self):
        # The exact case the old `sync_ok in ("FAILED","SKIPPED_OFFLINE")` check missed:
        # a real failure is a bool False, not a string, so it never triggered the banner.
        html, subject = daily_task._sync_warning(False, self.TODAY)
        self.assertIn("Synchronization Failed", html)
        self.assertIn("(Sync Failed)", subject)

    def test_true_sync_result_is_silent(self):
        html, subject = daily_task._sync_warning(True, self.TODAY)
        self.assertEqual(html, "")
        self.assertEqual(subject, f"AETHER Daily Rotation & Momentum Report: {self.TODAY}")

    def test_exception_produces_error_banner(self):
        html, subject = daily_task._sync_warning(
            None, self.TODAY, error=RuntimeError("UNC path down")
        )
        self.assertIn("Synchronization Error", html)
        self.assertIn("UNC path down", html)
        self.assertIn("(Sync Failed)", subject)


if __name__ == "__main__":
    unittest.main()
