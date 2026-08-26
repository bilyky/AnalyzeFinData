"""
Hermetic self-test for the pre-commit Documentation-Sentry drift guard
(`scripts/utils/pre_commit_validator.check_wiki_about_sync`).

The guard's contract:
  * SKIP (return True) when no wiki surface (Data/wiki.json / web/index.html) is staged, so
    unrelated commits are not slowed and the parity suite is not run needlessly.
  * PASS (True) when a wiki surface IS staged and the parity suite returns 0.
  * BLOCK (False) when the parity suite returns non-zero — real About<->wiki drift.
  * FAIL CLOSED (False) when the parity suite cannot even be run (a red/unrunnable guard must
    never silently let a commit through).

These are the branches a real commit depends on, so they are worth locking. The git `_staged_paths`
read and the parity subprocess are the only I/O seams — both are mocked, so this runs offline and
touches neither git nor the real test suite.
"""
import importlib.util
import os
import sys
import unittest
from unittest import mock

_VALIDATOR_PATH = os.path.join(
    os.path.dirname(__file__), "..", "scripts", "utils", "pre_commit_validator.py")
_spec = importlib.util.spec_from_file_location("pre_commit_validator", _VALIDATOR_PATH)
pcv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pcv)  # side-effect-free: main() is __main__-guarded


def _completed(returncode, stdout="", stderr=""):
    """Stand-in for subprocess.CompletedProcess with just the fields the guard reads."""
    return mock.Mock(returncode=returncode, stdout=stdout, stderr=stderr)


class TestWikiAboutGuard(unittest.TestCase):

    def test_skips_when_no_wiki_surface_staged(self):
        with mock.patch.object(pcv, "_staged_paths", return_value={"aether/etrade.py"}), \
             mock.patch.object(pcv, "subprocess") as m_sub:
            self.assertTrue(pcv.check_wiki_about_sync())
            m_sub.run.assert_not_called()   # the parity suite must NOT run for unrelated commits

    def test_passes_when_parity_suite_green(self):
        with mock.patch.object(pcv, "_staged_paths", return_value={"web/index.html"}), \
             mock.patch.object(pcv, "subprocess") as m_sub:
            m_sub.run.return_value = _completed(0)
            self.assertTrue(pcv.check_wiki_about_sync())
            m_sub.run.assert_called_once()

    def test_blocks_when_parity_suite_red(self):
        with mock.patch.object(pcv, "_staged_paths", return_value={"Data/wiki.json"}), \
             mock.patch.object(pcv, "subprocess") as m_sub:
            m_sub.run.return_value = _completed(1, stderr="AssertionError: orphaned card FOO")
            self.assertFalse(pcv.check_wiki_about_sync())

    def test_fails_closed_when_parity_suite_unrunnable(self):
        with mock.patch.object(pcv, "_staged_paths", return_value={"web/index.html"}), \
             mock.patch.object(pcv, "subprocess") as m_sub:
            m_sub.run.side_effect = OSError("python not found")
            self.assertFalse(pcv.check_wiki_about_sync())


if __name__ == "__main__":
    unittest.main()
