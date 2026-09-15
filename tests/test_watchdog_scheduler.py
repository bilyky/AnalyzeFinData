"""
Unit tests for watchdog.py's check_task_scheduler() — the LastTaskResult exit code auditor.
No real tasks registered; only the subprocess boundary (schtasks / PowerShell) is mocked, so
the real check_task_scheduler branching runs against fabricated tool output.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import watchdog


class TestWatchdogSchedulerAuditing(unittest.TestCase):
    @mock.patch("subprocess.run")
    @mock.patch("watchdog._log")
    def test_failed_last_task_result_logged_as_error_and_reported(self, mock_log, mock_run):
        """A task that exists but whose last run exited non-zero lands in `failed`, not `missing`."""
        # Mock 1: schtasks query returns 0 (task exists)
        mock_query = mock.MagicMock(returncode=0, stdout="AETHER_Morning Ready")
        # Mock 2: PowerShell query returns '1' (last task result was a crash)
        mock_ps_res = mock.MagicMock(returncode=0, stdout="1\n")

        # Configure subprocess.run side-effect
        mock_run.side_effect = [mock_query, mock_ps_res]

        # Temporarily override TASKS list to contain only our test target
        with mock.patch.object(watchdog, "TASKS", ["AETHER_Morning"]):
            missing, failed = watchdog.check_task_scheduler()

        # Assert that the error was caught and logged with the correct text
        mock_log.error.assert_called_once()
        self.assertIn("failed on its last execution (Exit Code: 1)", mock_log.error.call_args[0][0])

        # A crashed-but-present task alerts (via `failed`) but is NOT routed to re-registration.
        self.assertEqual(failed, ["AETHER_Morning"])
        self.assertEqual(missing, [])

    @mock.patch("subprocess.run")
    @mock.patch("watchdog._log")
    def test_successful_or_standard_result_not_logged_or_reported(self, mock_log, mock_run):
        """Benign SCHED_S_* codes (Success/Ready/Running/HasNotRun/NoMoreRuns/SomeTriggersFailed/Queued) are ignored."""
        # 0=Success, 267008=READY, 267009=RUNNING, 267011=HAS_NOT_RUN, 267012=NO_MORE_RUNS, 267035=SOME_TRIGGERS_FAILED, 267045=QUEUED
        for ok_code in ("0", "267008", "267009", "267011", "267012", "267035", "267045"):
            mock_log.reset_mock()
            mock_query = mock.MagicMock(returncode=0, stdout="AETHER_Morning Ready")
            mock_ps_res = mock.MagicMock(returncode=0, stdout=f"{ok_code}\n")
            mock_run.side_effect = [mock_query, mock_ps_res]

            with mock.patch.object(watchdog, "TASKS", ["AETHER_Morning"]):
                missing, failed = watchdog.check_task_scheduler()

            # Assert that NO error was logged and no tasks were returned as missing/failed
            mock_log.error.assert_not_called()
            self.assertEqual((missing, failed), ([], []), f"code {ok_code} should be benign")

    @mock.patch("subprocess.run")
    def test_missing_task_returns_in_missing_list(self, mock_run):
        """A task absent from Task Scheduler ('cannot find') lands in `missing`, not `failed`."""
        # schtasks query returns non-zero with "cannot find" text
        mock_query = mock.MagicMock(returncode=1, stdout="ERROR: The system cannot find the file specified.", stderr="")
        mock_run.return_value = mock_query

        with mock.patch.object(watchdog, "TASKS", ["AETHER_Morning"]):
            missing, failed = watchdog.check_task_scheduler()

        self.assertEqual((missing, failed), (["AETHER_Morning"], []))

    @mock.patch("subprocess.run")
    @mock.patch("watchdog._log")
    def test_audit_queries_the_same_folder_the_task_lives_in(self, mock_log, mock_run):
        """Regression for the silent no-op blocker: the PowerShell LastTaskResult audit must query
        the SAME location schtasks did. A root-level TASKS entry -> -TaskPath '\\' and
        -TaskName '<name>' — never a hardcoded '\\AETHER_Agents\\', which would find nothing and make
        the audit silently never fire."""
        mock_query = mock.MagicMock(returncode=0, stdout="AETHER_Morning Ready")
        mock_ps_res = mock.MagicMock(returncode=0, stdout="0\n")
        mock_run.side_effect = [mock_query, mock_ps_res]

        with mock.patch.object(watchdog, "TASKS", ["AETHER_Morning"]):
            watchdog.check_task_scheduler()

        # Second subprocess.run call is the PowerShell audit; inspect the argv it was handed.
        ps_argv = mock_run.call_args_list[1][0][0]
        ps_script = ps_argv[-1]
        self.assertIn("-TaskName 'AETHER_Morning'", ps_script)
        self.assertIn(r"-TaskPath '\'", ps_script)
        self.assertNotIn("AETHER_Agents", ps_script)


class TestWatchdogNightHoursSkip(unittest.TestCase):
    @mock.patch("watchdog.notify.send_email")
    @mock.patch("watchdog.is_pid_running")
    @mock.patch("watchdog.check_data_freshness")
    @mock.patch("watchdog.check_logs")
    @mock.patch("watchdog.powergauge.ensure_valid_session")
    @mock.patch("watchdog.etrade.scheduled_reauth")
    @mock.patch("watchdog._log")
    @mock.patch("watchdog.datetime")
    def test_night_hours_skips_heavy_diagnostics(self, mock_datetime, mock_log, mock_reauth, mock_session, mock_check_logs, mock_data_freshness, mock_is_pid, mock_send_email):
        """Outside active weekday operational window (e.g. Sunday night), watchdog exits early after session keeps."""
        import datetime as dt
        mock_is_pid.return_value = False
        mock_data_freshness.return_value = None
        # Mock current time to Sunday Sep 13, 2026, 11:00 PM (23:00)
        mock_datetime.datetime.now.return_value = dt.datetime(2026, 9, 13, 23, 0, 0)
        mock_datetime.datetime.now_la.return_value = dt.datetime(2026, 9, 13, 23, 0, 0)
        
        # Mock successful session keeps
        mock_reauth.return_value = {"ok": True, "reason": "renewed"}
        mock_session.return_value = {"jsessionid": "test_id"}

        # Execute run_watchdog
        watchdog.run_watchdog()

        # Verify session keeps WERE run
        mock_reauth.assert_called_once_with("production")
        mock_session.assert_called_once()

        # Verify that task/log audits WERE successfully run overnight (no blind spots!)
        self.assertEqual(mock_check_logs.call_count, 2)
        mock_log.info.assert_any_call("💤 [Night-Hours Skip] Skipping heavy process supervisor and port sentry overnight.")

    @mock.patch("watchdog.notify.send_email")
    @mock.patch("watchdog.is_pid_running")
    @mock.patch("watchdog.check_data_freshness")
    @mock.patch("watchdog.check_logs")
    @mock.patch("watchdog.powergauge.ensure_valid_session")
    @mock.patch("watchdog.etrade.scheduled_reauth")
    @mock.patch("watchdog._log")
    @mock.patch("watchdog.datetime")
    def test_active_hours_runs_full_diagnostics(self, mock_datetime, mock_log, mock_reauth, mock_session, mock_check_logs, mock_data_freshness, mock_is_pid, mock_send_email):
        """Inside active operational window (e.g. Monday morning 7:00 AM), watchdog runs full diagnostic suite."""
        import datetime as dt
        mock_is_pid.return_value = False
        mock_data_freshness.return_value = None
        # Mock current time to Monday Sep 14, 2026, 7:00 AM
        mock_datetime.datetime.now.return_value = dt.datetime(2026, 9, 14, 7, 0, 0)
        mock_datetime.datetime.now_la.return_value = dt.datetime(2026, 9, 14, 7, 0, 0)
        
        # Mock successful session keeps
        mock_reauth.return_value = {"ok": True, "reason": "renewed"}
        mock_session.return_value = {"jsessionid": "test_id"}
        mock_check_logs.return_value = []

        # Execute run_watchdog (expecting it to continue to step 1, which calls check_logs)
        try:
            watchdog.run_watchdog()
        except Exception:
            pass # we mock out subprocesses, so subsequent steps may raise, which is fine as long as check_logs was hit!

        # Verify session keeps WERE run
        mock_reauth.assert_called_once_with("production")
        mock_session.assert_called_once()

        # Verify check_logs WAS called (full diagnostics executed!)
        self.assertEqual(mock_check_logs.call_count, 2)


class TestRecoveryNextStepsFalseGreen(unittest.TestCase):
    """The recovery email's SECTION-4 "Next Steps" bullets must never present an
    OFF-MARKET run as a verified pass. `compilation_passed == "SKIPPED"` is a
    truthy string; the pre-fix code tested it truthily and rendered "pushed the
    fix to the main branch" under a SKIPPED (OFF-MARKET) badge — a false-green.
    These pin the strict tri-state rendering (would fail against truthy checks)."""

    def test_skipped_does_not_render_success_bullets(self):
        # Off-market healer run: the compile check was deliberately skipped.
        html = watchdog._recovery_next_steps_html("SKIPPED", ai_triggered=True)
        # The false-green: a skipped compile must NOT claim it pushed a fix or resumed.
        self.assertNotIn("pushed the fix to the main branch", html)
        self.assertNotIn("Automatic Resume", html)
        # …and skip is not failure, so no "failed to compile" alert either.
        self.assertNotIn("failed to compile", html)
        # The healer-triggered lock-file action is still legitimately shown.
        self.assertIn("delete the circuit breaker lock file", html)

    def test_verified_pass_renders_success_bullets(self):
        html = watchdog._recovery_next_steps_html(True, ai_triggered=True)
        self.assertIn("pushed the fix to the main branch", html)
        self.assertIn("Automatic Resume", html)
        self.assertNotIn("failed to compile", html)

    def test_verified_failure_renders_only_alert(self):
        html = watchdog._recovery_next_steps_html(False, ai_triggered=True)
        self.assertNotIn("pushed the fix to the main branch", html)
        self.assertNotIn("Automatic Resume", html)
        self.assertIn("failed to compile", html)

    def test_pass_without_healer_omits_healer_bullets(self):
        # Nominal in-window pass, nothing healed: no push-fix claim, no lock action.
        html = watchdog._recovery_next_steps_html(True, ai_triggered=False)
        self.assertNotIn("pushed the fix to the main branch", html)
        self.assertIn("Automatic Resume", html)
        self.assertNotIn("delete the circuit breaker lock file", html)


if __name__ == "__main__":
    unittest.main()
