"""
Tests for scripts/diagnostics/preflight_validator.py — filesystem integrity /
lock checks and the status-briefing email verdict.
"""
import os
import sys
import tempfile
import time
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

    @mock.patch("preflight_validator.subprocess.run")
    def test_pipeline_lock_other_pid_detected(self, mock_run):
        # Test 2: When lock has a DIFFERENT PID, it is flagged.
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "Data").mkdir()
            lock_file = base / "Data" / "pipeline_run.lock"
            lock_file.write_text("999999") # totally different PID

            mock_run.return_value = mock.Mock(stdout="999999")

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

class TestPreflightEtradeWaiver(unittest.TestCase):
    # check_etrade_api drives the unattended re-auth door scheduled_reauth() (renew-first,
    # at most one HEADLESS breaker/trust-gated mint) rather than get_tokens(allow_browser=
    # False), whose default HEADFUL mint needs an interactive desktop.
    #
    # Market-closed is decided by the ET weekday (token life follows ET midnight). On an ET
    # weekend it never mints: renew-only keep_alive, else WAIVED. On a weekday the verdict
    # follows scheduled_reauth's result["ok"]; no reauth lock is held in these cases, so a
    # breaker/in_progress result fails immediately (the in-flight wait is its own suite).
    SAT, WED = 5, 2

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        lock = mock.patch.object(pf.etrade, "_REAUTH_LOCK_PATH", os.path.join(self._tmp.name, "reauth.lock"))
        lock.start()
        self.addCleanup(lock.stop)

    @mock.patch("time.sleep")
    @mock.patch("aether.etrade.keep_alive")
    @mock.patch("aether.etrade.scheduled_reauth")
    @mock.patch("datetime.datetime")
    def test_check_etrade_api_verdict_matrix(self, mock_datetime, mock_reauth, mock_keep, mock_sleep):
        live = {"oauth_token": "t"}
        cases = [
            # ET weekday, keep_alive, scheduled_reauth result,        expected, door called
            (self.SAT, None, None,                                    "WAIVED", False),  # weekend: never mints
            (self.SAT, live, None,                                    True,     False),  # weekend: live token renewed
            (self.WED, None, {"ok": False, "reason": "failed"},       False,    True),
            (self.WED, None, {"ok": False, "reason": "blocked"},      False,    True),
            (self.WED, None, {"ok": False, "reason": "breaker"},      False,    True),   # cooling, no mint in flight
            (self.WED, None, {"ok": False, "reason": "in_progress"},  False,    True),   # lock not held -> no wait
            (self.WED, None, {"ok": True,  "reason": "reauthed"},     True,     True),
            (self.WED, None, {"ok": True,  "reason": "renewed"},      True,     True),
        ]
        for weekday, keep, result, expected, door in cases:
            with self.subTest(weekday=weekday, keep=bool(keep), result=result):
                mock_dt = mock.Mock()
                mock_dt.weekday.return_value = weekday
                mock_datetime.now.return_value = mock_dt
                for m in (mock_reauth, mock_keep, mock_sleep):
                    m.reset_mock()
                mock_keep.return_value = keep
                mock_reauth.return_value = result

                self.assertEqual(pf.check_etrade_api(), expected)
                tz = mock_datetime.now.call_args.args[0]
                self.assertEqual(str(tz), "America/New_York")        # ET date, not host-local
                if door:
                    mock_reauth.assert_called_once_with("production")
                else:
                    mock_reauth.assert_not_called()                  # no mint on an ET weekend
                mock_sleep.assert_not_called()


class TestPreflightInflightMint(unittest.TestCase):
    """A weekday preflight that collides with another door's in-flight mint waits for it
    (renew-only, bounded) instead of aborting the 5:30 pipeline. Real lock-file checks;
    only the broker door, keep_alive, the clock and sleep are mocked."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.lock = os.path.join(self._tmp.name, "reauth.lock")
        for p in (mock.patch.object(pf.etrade, "_REAUTH_LOCK_PATH", self.lock),
                  mock.patch("datetime.datetime"),
                  mock.patch("time.sleep")):
            m = p.start()
            self.addCleanup(p.stop)
            if p.attribute == "datetime":
                m.now.return_value.weekday.return_value = 2          # Wednesday ET
            if p.attribute == "sleep":
                self.sleep = m

    def _hold_lock(self, age_sec=0.0):
        Path(self.lock).write_text("")
        t = time.time() - age_sec
        os.utime(self.lock, (t, t))

    def test_mint_in_flight_only_for_a_fresh_lock(self):
        self.assertFalse(pf._mint_in_flight())                       # no lock file
        self._hold_lock()
        self.assertTrue(pf._mint_in_flight())                        # fresh lock = live mint
        self._hold_lock(age_sec=pf._INFLIGHT_MINT_WAIT_SEC + 5)
        self.assertFalse(pf._mint_in_flight())                       # stale/leaked lock

    def test_waits_for_concurrent_mint_then_passes(self):
        self._hold_lock()
        for reason in ("in_progress", "breaker"):
            with self.subTest(reason=reason),                  mock.patch("aether.etrade.scheduled_reauth", return_value={"ok": False, "reason": reason}),                  mock.patch("aether.etrade.keep_alive", side_effect=[None, {"oauth_token": "t"}]) as m_keep:
                self.sleep.reset_mock()
                self.assertTrue(pf.check_etrade_api())
                self.assertEqual(m_keep.call_count, 2)
                self.sleep.assert_called_once_with(pf._INFLIGHT_MINT_POLL_SEC)

    def test_stops_waiting_when_the_other_mint_ends_without_a_token(self):
        self._hold_lock()
        def release_lock(_):
            os.remove(self.lock)                                     # the other door finished (failed)
        self.sleep.side_effect = release_lock
        with mock.patch("aether.etrade.scheduled_reauth", return_value={"ok": False, "reason": "breaker"}),              mock.patch("aether.etrade.keep_alive", return_value=None) as m_keep:
            self.assertFalse(pf.check_etrade_api())
        self.assertEqual(m_keep.call_count, 2)                       # one last look after release
        self.sleep.assert_called_once()

    def test_gives_up_at_the_wait_bound(self):
        self._hold_lock()
        clock = iter([0.0, 0.0, pf._INFLIGHT_MINT_WAIT_SEC + 1])
        with mock.patch("aether.etrade.scheduled_reauth", return_value={"ok": False, "reason": "in_progress"}),              mock.patch("aether.etrade.keep_alive", return_value=None),              mock.patch("time.monotonic", side_effect=lambda: next(clock)):
            self.assertFalse(pf.check_etrade_api())
        self.sleep.assert_called_once()


if __name__ == "__main__":
    unittest.main()
