"""
Unit tests for watchdog.py's check_task_scheduler() — the LastTaskResult exit code auditor.
No real tasks registered; all mocked.
"""
import os
import sys
import unittest
import subprocess
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import watchdog


class TestWatchdogSchedulerAuditing(unittest.TestCase):
    @mock.patch("subprocess.run")
    @mock.patch("watchdog._log")
    def test_failed_last_task_result_logged_as_error_and_reported(self, mock_log, mock_run):
        """Verify that a task returning a non-zero, non-standard exit code (e.g. 1) is logged and returned as failed/missing."""
        # Mock 1: schtasks query returns 0 (task exists)
        mock_query = mock.MagicMock(returncode=0, stdout="AETHER_Morning Ready")
        # Mock 2: PowerShell query returns '1' (last task result was a crash)
        mock_ps_res = mock.MagicMock(returncode=0, stdout="1\n")
        
        # Configure subprocess.run side-effect
        mock_run.side_effect = [mock_query, mock_ps_res]
        
        # Temporarily override TASKS list to contain only our test target
        with mock.patch.object(watchdog, "TASKS", ["AETHER_Morning"]):
            failed_tasks = watchdog.check_task_scheduler()
            
        # Assert that the error was caught and logged with the correct text
        mock_log.error.assert_called_once()
        self.assertIn("failed on its last execution (Exit Code: 1)", mock_log.error.call_args[0][0])
        
        # Assert that the failed task was returned in the list so watchdog immediately triggers email alerts!
        self.assertEqual(failed_tasks, ["AETHER_Morning"])

    @mock.patch("subprocess.run")
    @mock.patch("watchdog._log")
    def test_successful_or_standard_result_not_logged_or_reported(self, mock_log, mock_run):
        """Verify that standard codes like 0 (Success) or 267011 (New task) are ignored and NOT logged or returned."""
        # Iterate over all non-error codes
        for ok_code in ("0", "267011", "267008", "267012"):
            mock_log.reset_mock()
            mock_query = mock.MagicMock(returncode=0, stdout="AETHER_Morning Ready")
            mock_ps_res = mock.MagicMock(returncode=0, stdout=f"{ok_code}\n")
            mock_run.side_effect = [mock_query, mock_ps_res]
            
            with mock.patch.object(watchdog, "TASKS", ["AETHER_Morning"]):
                failed_tasks = watchdog.check_task_scheduler()
                
            # Assert that NO error was logged and no tasks were returned as missing/failed
            mock_log.error.assert_not_called()
            self.assertEqual(failed_tasks, [])

    @mock.patch("subprocess.run")
    def test_missing_task_returns_in_missing_list(self, mock_run):
        """Verify that a task that does not exist on the machine is returned in the missing tasks list."""
        # schtasks query returns non-zero with "cannot find" text
        mock_query = mock.MagicMock(returncode=1, stdout="ERROR: The system cannot find the file specified.", stderr="")
        mock_run.return_value = mock_query
        
        with mock.patch.object(watchdog, "TASKS", ["AETHER_Morning"]):
            missing = watchdog.check_task_scheduler()
            
        self.assertEqual(missing, ["AETHER_Morning"])


if __name__ == "__main__":
    unittest.main()
