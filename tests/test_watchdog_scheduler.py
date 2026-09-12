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
        """Benign SCHED_S_* codes (Success/Ready/Running/HasNotRun/NoMoreRuns/Queued) are ignored."""
        # 0=Success, 267008=READY, 267009=RUNNING, 267011=HAS_NOT_RUN, 267012=NO_MORE_RUNS, 267035=QUEUED
        for ok_code in ("0", "267008", "267009", "267011", "267012", "267035"):
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


if __name__ == "__main__":
    unittest.main()
