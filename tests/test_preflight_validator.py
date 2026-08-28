"""
Tests for scripts/diagnostics/preflight_validator.py — filesystem integrity /
lock checks and the status-briefing email verdict.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "diagnostics"))
import preflight_validator as pf


class TestFileAndDirectoryIntegrity(unittest.TestCase):
    def test_all_present_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "Data" / "Backup").mkdir(parents=True)
            (base / "Data" / "Symbol_full").mkdir(parents=True)
            (base / "config.json").write_text("{}")
            (base / "Data" / "state_of_the_day.xlsx").write_text("x")

            ok, missing = pf.check_file_and_directory_integrity(base_dir=base)

            self.assertTrue(ok)
            self.assertEqual(missing, [])

    def test_missing_items_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            # Only Data exists; Backup, Symbol_full, config.json, xlsx all absent.
            (base / "Data").mkdir()

            ok, missing = pf.check_file_and_directory_integrity(base_dir=base)

            self.assertFalse(ok)
            self.assertIn("Directory: Backup", missing)
            self.assertIn("Directory: Symbol_full", missing)
            self.assertIn("File: config.json", missing)
            self.assertIn("File: state_of_the_day.xlsx", missing)


class TestActiveLocks(unittest.TestCase):
    def test_clean_when_no_lockfiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "Data").mkdir()
            # An unlocked xlsx must NOT be flagged (rename-to-self is a no-op).
            (base / "Data" / "state_of_the_day.xlsx").write_text("x")

            ok, locks = pf.check_active_locks(base_dir=base)

            self.assertTrue(ok)
            self.assertEqual(locks, [])

    def test_pipeline_and_rapidapi_locks_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "Data").mkdir()
            (base / "Data" / "pipeline_run.lock").write_text("")
            (base / "Data" / "rapidapi.lock").write_text("")

            ok, locks = pf.check_active_locks(base_dir=base)

            self.assertFalse(ok)
            self.assertTrue(any("pipeline_run.lock" in x for x in locks))
            self.assertTrue(any("rapidapi.lock" in x for x in locks))


class TestSendPreflightEmail(unittest.TestCase):
    def _send(self, all_ok, imap=True, smtp=True, chaikin=True,
              etrade=True, integrity=True, locks=True):
        # Mirrors the (label, ok, kind) roster the caller builds in
        # run_preflight_diagnostics — the single source of truth for the table.
        checks = [
            ("Gmail IMAP",                  imap,      "conn"),
            ("Gmail SMTP Dispatch",         smtp,      "conn"),
            ("Chaikin PowerGauge API",      chaikin,   "conn"),
            ("E*TRADE Brokerage OAuth",     etrade,    "conn"),
            ("File & Directory Integrity",  integrity, "conn"),
            ("Active Process & File Locks", locks,     "lock"),
        ]
        with mock.patch.object(pf.notify, "send_email") as send:
            pf.send_preflight_email(
                checks, missing_items=[], active_locks=[], duration=1.0, all_ok=all_ok)
        return send

    def test_success_verdict_uses_passed_all_ok(self):
        send = self._send(all_ok=True)
        send.assert_called_once()
        args, kwargs = send.call_args
        subject, body = args[0], args[1]
        self.assertIn("Pre-Flight", subject)
        self.assertTrue(kwargs.get("is_html"))
        self.assertIn("[SUCCESS]", body)
        self.assertNotIn("[ALERT]", body)

    def test_alert_verdict_follows_all_ok_not_local_recompute(self):
        # Every individual check is True, but the caller's verdict is False.
        # The email MUST honour all_ok (single source of truth), not recompute.
        send = self._send(all_ok=False)
        body = send.call_args[0][1]
        self.assertIn("[ALERT]", body)
        self.assertNotIn("[SUCCESS]", body)

    def test_roster_renders_pass_fail_and_lock_badges_from_checks(self):
        # A failing connection check renders [FAIL]; the lock-kind check renders
        # its CLEAN/LOCKED badge, not PASS/FAIL — proving both the labels and the
        # badge kind come from the shared roster the caller passes in.
        send = self._send(all_ok=False, chaikin=False, locks=False)
        body = send.call_args[0][1]
        self.assertIn("Chaikin PowerGauge API", body)
        self.assertIn("[FAIL]", body)
        self.assertIn("[LOCKED]", body)
        # An all-pass conn check still shows [PASS], and the lock badge is never PASS/FAIL.
        self.assertIn("[PASS]", body)


class TestNewPreflightFeatures(unittest.TestCase):
    def test_pipeline_lock_self_waiver(self):
        # Test 1: When lock has OUR own PID, it is waived.
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "Data").mkdir()
            lock_file = base / "Data" / "pipeline_run.lock"
            lock_file.write_text(str(os.getpid()))

            with mock.patch("os.getpid", return_value=os.getpid()):
                ok, locks = pf.check_active_locks(base_dir=base)
                self.assertTrue(ok)
                self.assertEqual(locks, [])

    def test_pipeline_lock_other_pid_detected(self):
        # Test 2: When lock has a DIFFERENT PID, it is flagged.
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "Data").mkdir()
            lock_file = base / "Data" / "pipeline_run.lock"
            lock_file.write_text("999999") # totally different PID

            ok, locks = pf.check_active_locks(base_dir=base)
            self.assertFalse(ok)
            self.assertIn("Pipeline Active Lock (pipeline_run.lock)", locks)

    def test_pipeline_lock_corrupt_file_handled(self):
        # Test 3: When lock file has non-integer or empty contents, it falls back to 0.
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "Data").mkdir()
            lock_file = base / "Data" / "pipeline_run.lock"
            lock_file.write_text("not_a_pid")

            ok, locks = pf.check_active_locks(base_dir=base)
            self.assertFalse(ok)
            self.assertIn("Pipeline Active Lock (pipeline_run.lock)", locks)

    @mock.patch("subprocess.run")
    def test_check_pipeline_smoke_test_skipped_without_flag(self, mock_run):
        # Test 4: By default, if --smoke is not in sys.argv, smoke test is skipped.
        with mock.patch("sys.argv", ["preflight_validator.py"]):
            ok, issues = pf.check_pipeline_smoke_test()
            self.assertTrue(ok)
            self.assertEqual(issues, [])
            mock_run.assert_not_called()

    @mock.patch("subprocess.run")
    def test_check_pipeline_smoke_test_run_success(self, mock_run):
        # Test 5: If --smoke in sys.argv, it runs and passes on 0 exit code.
        mock_res = mock.Mock()
        mock_res.returncode = 0
        mock_run.return_value = mock_res

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "autonomous_pipeline.py").write_text("print('smoke')")

            with mock.patch("sys.argv", ["preflight_validator.py", "--smoke"]):
                ok, issues = pf.check_pipeline_smoke_test(base_dir=base)
                self.assertTrue(ok)
                self.assertEqual(issues, [])
                mock_run.assert_called_once()

    @mock.patch("subprocess.run")
    def test_check_pipeline_smoke_test_run_failure(self, mock_run):
        # Test 6: If --smoke runs and fails, it returns True (advisory) but lists the issue.
        mock_res = mock.Mock()
        mock_res.returncode = 1
        mock_res.stderr = "ImportError"
        mock_run.return_value = mock_res

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "autonomous_pipeline.py").write_text("raise ValueError")

            with mock.patch("sys.argv", ["preflight_validator.py", "--smoke"]):
                ok, issues = pf.check_pipeline_smoke_test(base_dir=base)
                # Advisory: should still return True!
                self.assertTrue(ok)
                self.assertTrue(any("Pipeline smoke test failed with Exit Code 1" in x for x in issues))

    @mock.patch("subprocess.run")
    def test_check_scheduled_tasks_integrity_with_no_duplicates(self, mock_run):
        # Test 7: Scheduled tasks query with no duplicates passes.
        mock_res = mock.Mock()
        mock_res.returncode = 0
        # Simulated list output with single tasks
        mock_res.stdout = (
            "TaskName: \\AETHER_Agents\\AETHER_Watchdog\n"
            "Task To Run: python watchdog.py\n\n"
            "TaskName: \\AETHER_Agents\\AETHER_DailyDriver\n"
            "Task To Run: python daily_task.py\n\n"
        )
        mock_run.return_value = mock_res

        ok, issues = pf.check_scheduled_tasks_integrity()
        self.assertTrue(ok)
        self.assertEqual(issues, [])


if __name__ == "__main__":
    unittest.main()
