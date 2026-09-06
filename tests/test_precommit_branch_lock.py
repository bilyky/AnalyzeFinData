import unittest
from unittest import mock
import os
import sys
from pathlib import Path

# Add repo root to PATH so scripts can be imported cleanly
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

# Import pre_commit_validator
import scripts.utils.pre_commit_validator as pcval

class TestBranchSafetyLock(unittest.TestCase):
    @mock.patch("subprocess.run")
    def test_branch_safety_lock_blocks_on_main(self, mock_run):
        # Mock subprocess.run to simulate active branch is "main"
        mock_res = mock.Mock()
        mock_res.stdout = "main\n"
        mock_res.returncode = 0
        mock_run.return_value = mock_res

        # Test block: without env override, it should return False (blocked!)
        with mock.patch.dict("os.environ", {}, clear=True):
            ok = pcval.check_no_direct_main_commit()
            self.assertFalse(ok)

    @mock.patch("subprocess.run")
    def test_branch_safety_lock_blocks_on_master(self, mock_run):
        # Mock subprocess.run to simulate active branch is "master"
        mock_res = mock.Mock()
        mock_res.stdout = "master\n"
        mock_res.returncode = 0
        mock_run.return_value = mock_res

        # Test block: without env override, it should return False (blocked!)
        with mock.patch.dict("os.environ", {}, clear=True):
            ok = pcval.check_no_direct_main_commit()
            self.assertFalse(ok)

    @mock.patch("subprocess.run")
    def test_branch_safety_lock_passes_on_feature_branch(self, mock_run):
        # Mock subprocess.run to simulate active branch is "feat/etrade-senior-enhancements"
        mock_res = mock.Mock()
        mock_res.stdout = "feat/etrade-senior-enhancements\n"
        mock_res.returncode = 0
        mock_run.return_value = mock_res

        # On feature branches, it should return True (allowed!)
        with mock.patch.dict("os.environ", {}, clear=True):
            ok = pcval.check_no_direct_main_commit()
            self.assertTrue(ok)

    @mock.patch("subprocess.run")
    def test_branch_safety_lock_honors_env_override_on_main(self, mock_run):
        # Mock subprocess.run to simulate active branch is "main"
        mock_res = mock.Mock()
        mock_res.stdout = "main\n"
        mock_res.returncode = 0
        mock_run.return_value = mock_res

        # With AETHER_ALLOW_DIRECT_MAIN_COMMIT=1, it should bypass the block and return True!
        with mock.patch.dict("os.environ", {"AETHER_ALLOW_DIRECT_MAIN_COMMIT": "1"}):
            ok = pcval.check_no_direct_main_commit()
            self.assertTrue(ok)

if __name__ == "__main__":
    unittest.main()
