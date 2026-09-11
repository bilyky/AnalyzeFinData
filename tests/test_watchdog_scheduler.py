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
    def test_failed_last_task_result_logged_as_error(self, mock_log, mock_run):
        """Verify that a task returning a non-zero, non-standard exit code (e.g. 1) is logged as an error."""
        # Mock 1: schtasks query returns 0 (task exists)
        mock_query = mock.MagicMock(returncode=0, stdout="AETHER_Morning Ready")
        # Mock 2: PowerShell query returns '1' (last task result was a crash)
        mock_ps_res = mock.MagicMock(returncode=0, stdout="1\n")
        
        # Configure subprocess.run side-effect
        mock_run.side_effect = [mock_query, mock_ps_res]
        
        # Temporarily override TASKS list to contain only our test target
        with mock.patch.object(watchdog, "TASKS", ["AETHER_Morning"]):
            watchdog.check_task_scheduler()
            
        print("MOCK RUN CALLS:", mock_run.mock_calls)
        print("MOCK LOG CALLS:", mock_log.mock_calls)
        
        # Assert that the error was caught and logged with the correct text
        mock_log.error.assert_called_once()
        self.assertIn("failed on its last execution (Exit Code: 1)", mock_log.error.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
