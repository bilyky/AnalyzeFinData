import unittest
from unittest import mock
import subprocess
from pathlib import Path

# Add project root to sys.path so we can import watchdog
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import watchdog

class TestWatchdogSync(unittest.TestCase):

    @mock.patch("watchdog.is_market_hours", return_value=False)
    @mock.patch("os.path.exists", return_value=True)
    @mock.patch.object(Path, "exists", return_value=True)
    @mock.patch.object(Path, "mkdir")
    @mock.patch("subprocess.run")
    def test_sync_timeout_returns_false(self, mock_run, mock_mkdir, mock_exists, mock_os_exists, mock_market):
        # Simulate a robocopy timeout
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="robocopy", timeout=120)
        
        result = watchdog.sync_data_folder()
        
        # Verify the contract: a timeout MUST return False, not True
        self.assertFalse(result)

if __name__ == "__main__":
    unittest.main()
