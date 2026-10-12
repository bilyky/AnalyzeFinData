"""prune_merged_worktrees.branch_is_lossless and its use in plan().

A finished PR does not prove its local branch is safe to delete: commits made after the last push,
or never pushed, exist only locally. These tests build real git repositories (a bare "origin" and a
clone) in a temp dir, so the reachability and merge-tree checks run against real git, not mocks.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "utils"))
import prune_merged_worktrees as pmw  # noqa: E402


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _commit(cwd, name, text):
    Path(cwd, name).write_text(text, encoding="utf-8")
    _git(cwd, "add", name)
    _git(cwd, "commit", "-q", "-m", f"edit {name}")


class TestBranchIsLossless(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self._tmp.name)
        self.origin, self.work = root / "origin.git", root / "work"
        _git(root, "init", "-q", "--bare", "-b", "main", str(self.origin))
        _git(root, "clone", "-q", str(self.origin), str(self.work))
        for k, v in (("user.name", "t"), ("user.email", "t@example.com"), ("commit.gpgsign", "false")):
            _git(self.work, "config", k, v)
        _commit(self.work, "a.txt", "base\n")
        _git(self.work, "push", "-q", "origin", "HEAD:main")
        _git(self.work, "fetch", "-q", "origin")
        self._cwd = os.getcwd()
        os.chdir(self.work)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _branch(self, name, *files):
        _git(self.work, "switch", "-q", "-c", name, "origin/main")
        for f, text in files:
            _commit(self.work, f, text)
        _git(self.work, "switch", "-q", "--detach", "origin/main")

    def test_branch_pushed_only_to_its_pr_head_is_lossless(self):
        self._branch("feat/pushed", ("b.txt", "feature\n"))
        _git(self.work, "push", "-q", "origin", "feat/pushed:refs/pull/1/head")
        self.assertTrue(pmw.branch_is_lossless("feat/pushed", 1))

    def test_commit_after_the_last_push_is_not_lossless(self):
        self._branch("feat/later", ("b.txt", "feature\n"))
        _git(self.work, "push", "-q", "origin", "feat/later:refs/pull/2/head")
        _git(self.work, "switch", "-q", "feat/later")
        _commit(self.work, "c.txt", "only local\n")
        _git(self.work, "switch", "-q", "--detach", "origin/main")
        self.assertFalse(pmw.branch_is_lossless("feat/later", 2))

    def test_never_pushed_branch_is_not_lossless(self):
        self._branch("feat/local", ("d.txt", "never pushed\n"))
        self.assertFalse(pmw.branch_is_lossless("feat/local", 3))

    def test_squash_merged_branch_with_its_content_on_main_is_lossless(self):
        self._branch("feat/squashed", ("e.txt", "squashed\n"))   # not pushed anywhere
        _git(self.work, "switch", "-q", "-c", "squash", "origin/main")
        _commit(self.work, "e.txt", "squashed\n")                 # same content, new commit
        _git(self.work, "push", "-q", "origin", "HEAD:main")
        _git(self.work, "fetch", "-q", "origin")
        _git(self.work, "switch", "-q", "--detach", "origin/main")
        self.assertTrue(pmw.branch_is_lossless("feat/squashed", 4))

    def test_private_pr_ref_is_cleaned_up(self):
        self._branch("feat/pushed2", ("f.txt", "x\n"))
        _git(self.work, "push", "-q", "origin", "feat/pushed2:refs/pull/5/head")
        pmw.branch_is_lossless("feat/pushed2", 5)
        refs = subprocess.run(["git", "for-each-ref", "refs/prune"], cwd=self.work,
                              capture_output=True, text=True).stdout
        self.assertEqual(refs.strip(), "")

    def test_missing_branch_is_not_lossless(self):
        self.assertFalse(pmw.branch_is_lossless("no/such-branch", 6))


class TestPlanKeepsUnpushedWork(unittest.TestCase):
    """plan() puts a finished PR's branch in KEEP, not PRUNE, when it isn't lossless."""

    def _plan(self, lossless):
        states = {"feat/wt": {"number": 7, "state": "closed", "merged": True},
                  "feat/solo": {"number": 8, "state": "closed", "merged": True}}

        def fake_run(cmd, check=True):
            if cmd[:2] == ["git", "rev-parse"]:
                return 0, "/repo/main\n", ""
            if cmd[:2] == ["git", "branch"]:
                return 0, "main\nfeat/wt\nfeat/solo\n", ""
            return 0, "", ""

        with mock.patch.object(pmw, "pr_state_map", return_value=states), \
             mock.patch.object(pmw, "list_worktrees",
                               return_value=[{"path": "/repo/_wt_feat", "branch": "feat/wt", "detached": False}]), \
             mock.patch.object(pmw, "current_branch", return_value="main"), \
             mock.patch.object(pmw, "is_dirty", return_value=False), \
             mock.patch.object(pmw, "links_inside", return_value=[]), \
             mock.patch.object(pmw, "branch_is_lossless", return_value=lossless), \
             mock.patch.object(pmw, "_run", side_effect=fake_run):
            return pmw.plan(None, "o/r", want_merged=True, want_closed=True)

    def test_not_lossless_is_kept_for_both_worktree_and_branch_only(self):
        actions, keep = self._plan(lossless=False)
        self.assertEqual(actions, [])
        reasons = " ".join(r for _, r in keep)
        self.assertIn("PR #7 finished but branch feat/wt has commits not on the remote", reasons)
        self.assertIn("PR #8 finished but the branch has commits not on the remote", reasons)

    def test_lossless_is_pruned(self):
        actions, _ = self._plan(lossless=True)
        self.assertEqual(sorted((k, b) for k, _, b, _ in actions), [("branch", "feat/solo"), ("worktree", "feat/wt")])


if __name__ == "__main__":
    unittest.main()
