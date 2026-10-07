"""mutate_scenario_steps.py refuses to run in the main checkout.

The script rewrites aether/scenario/steps.py with deliberate bugs while it runs. Once the
refactor's REPLACE phase wires steps.py into the live game, doing that in the main
checkout (where the live app runs) would put buggy code under live processes.
"""
import os
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "utils"))
import mutate_scenario_steps as mutate


def _norm(p):
    return os.path.normcase(os.path.realpath(p))


class TestMainCheckoutGuard(unittest.TestCase):
    def test_detection_matches_git_worktree_list(self):
        # The first `git worktree list` entry is always the main working tree, in CI's
        # plain checkout and in a linked worktree alike.
        out = subprocess.run(["git", "worktree", "list", "--porcelain"], cwd=mutate.ROOT,
                             capture_output=True, text=True, check=True).stdout
        main_tree = next(line[len("worktree "):] for line in out.splitlines() if line.startswith("worktree "))
        self.assertEqual(mutate._is_main_checkout(), _norm(main_tree) == _norm(mutate.ROOT))

    def test_refuses_in_the_main_checkout_before_touching_steps(self):
        untouchable = mock.MagicMock(side_effect=AssertionError("must not run"))
        with mock.patch.object(mutate, "_is_main_checkout", return_value=True), \
             mock.patch.object(mutate, "_steps_is_clean", untouchable), \
             mock.patch.object(mutate, "_run_tests", untouchable), \
             mock.patch.object(sys, "argv", ["mutate_scenario_steps.py"]), \
             mock.patch.object(sys, "stderr"):
            self.assertEqual(mutate.main(), 2)
        untouchable.assert_not_called()

    def test_explicit_override_is_honored(self):
        with mock.patch.object(mutate, "_is_main_checkout", return_value=True), \
             mock.patch.object(mutate, "_steps_is_clean", return_value=False), \
             mock.patch.object(sys, "argv", ["mutate_scenario_steps.py", "--allow-main-checkout"]), \
             mock.patch.object(sys, "stderr"):
            # Past the guard, the next check (dirty steps.py) stops it; still nothing mutated.
            self.assertEqual(mutate.main(), 2)


if __name__ == "__main__":
    unittest.main()
