"""A pipeline step never waits for typed input.

PROD 2026-11-24 17:01: the Chaikin browser login failed in run_history.py and main.py; login() then
asked on the console for a JSESSIONID, and each step sat until daily_task's 1800 s kill. The step
runners now give children stdin=DEVNULL, so a prompt reads end-of-file at once.
"""
import ast
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tests  # noqa: F401  hermetic harness
import daily_task

_REPO = os.path.join(os.path.dirname(__file__), "..")


class RunCommandTest(unittest.TestCase):
    def test_a_child_asking_for_input_does_not_hang(self):
        """A real child process that calls input(), as login() does after a failed browser login."""
        with tempfile.TemporaryDirectory() as d:
            script = os.path.join(d, "asks.py")
            with open(script, "w") as f:
                f.write("try:\n    input('Or paste a JSESSIONID here: ')\nexcept EOFError:\n    print('eof')\n")
            daily_task._failed_steps.clear()
            start = time.monotonic()
            with mock.patch.object(daily_task, "Path") as fake_path:
                # run_command rewrites argv[1] to the repo copy of the script; keep ours
                fake_path.return_value.resolve.return_value.parent.__truediv__.return_value = script
                out = daily_task.run_command([sys.executable, "asks.py"], timeout=20)
            took = time.monotonic() - start
        self.assertIn("eof", out)
        self.assertLess(took, 15)
        self.assertEqual(daily_task._failed_steps, [])

    def test_run_command_passes_devnull_stdin(self):
        with mock.patch.object(daily_task.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            daily_task.run_command([sys.executable, "main.py"], timeout=5)
        self.assertIs(run.call_args.kwargs.get("stdin"), daily_task.subprocess.DEVNULL)


class MorningPipelineTest(unittest.TestCase):
    def test_every_step_launch_has_devnull_stdin(self):
        """Every subprocess.run that starts a .py step in autonomous_pipeline passes stdin=DEVNULL."""
        src = open(os.path.join(_REPO, "autonomous_pipeline.py"), encoding="utf-8").read()
        found = 0
        for node in ast.walk(ast.parse(src)):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "run" and getattr(node.func.value, "id", "") == "subprocess"):
                seg = ast.get_source_segment(src, node) or ""
                if "sys.executable" in seg:       # a Python step, not the PowerShell process cleanup
                    found += 1
                    kw = {k.arg: ast.get_source_segment(src, k.value) for k in node.keywords}
                    self.assertEqual(kw.get("stdin"), "subprocess.DEVNULL", seg[:120])
        self.assertGreaterEqual(found, 2)       # main.py and run_history.py


if __name__ == "__main__":
    unittest.main()
