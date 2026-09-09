"""
Tests for the Windows PID-reuse overlap guard — watchdog.is_pid_running().

The guard must treat a PID as "our job still running" ONLY when a *python*
process holds it. Windows recycles PIDs rapidly, so a reused PID now owned by
chrome.exe / svchost must NOT block the watchdog. Only subprocess (the tasklist
call) is patched; the real is_pid_running decision logic runs, so a regression
in the image-name check fails here rather than silently allowing/blocking runs.

Red-green: against the pre-change guard (PID-present alone → True) the
chrome.exe case below returns True and this test FAILS; the branch's
image-name check makes it return False.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import watchdog


def _tasklist(stdout):
    """A fake completed-process carrying only the stdout the guard reads."""
    return mock.Mock(stdout=stdout)


class TestIsPidRunning(unittest.TestCase):
    # tasklist /NH row shapes (image name, PID, session, mem)
    PY = '"python.exe","1234","Console","0","54,321 K"'
    PYW = '"pythonw.exe","1234","Console","0","54,321 K"'
    CHROME = '"chrome.exe","1234","Console","0","250,000 K"'
    NONE = "INFO: No tasks are running which match the specified criteria."

    def _run(self, stdout, pid=1234):
        with mock.patch("subprocess.run", return_value=_tasklist(stdout)) as sp:
            result = watchdog.is_pid_running(pid)
        return result, sp

    def test_python_process_is_running(self):
        result, sp = self._run(self.PY)
        self.assertTrue(result)
        sp.assert_called_once()  # tasklist actually consulted

    def test_pythonw_process_is_running(self):
        # background pythonw.exe still contains "python" — must count
        self.assertTrue(self._run(self.PYW)[0])

    def test_reused_pid_owned_by_chrome_is_not_running(self):
        # the whole point of the fix: PID present, but not a python image
        self.assertFalse(self._run(self.CHROME)[0])

    def test_no_matching_task_is_not_running(self):
        self.assertFalse(self._run(self.NONE)[0])

    def test_nonpositive_pid_short_circuits_without_subprocess(self):
        with mock.patch("subprocess.run") as sp:
            self.assertFalse(watchdog.is_pid_running(0))
            self.assertFalse(watchdog.is_pid_running(-5))
        sp.assert_not_called()

    def test_subprocess_failure_is_treated_as_not_running(self):
        with mock.patch("subprocess.run", side_effect=OSError("tasklist missing")):
            self.assertFalse(watchdog.is_pid_running(1234))


if __name__ == "__main__":
    unittest.main()
