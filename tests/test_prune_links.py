"""prune_merged_worktrees never removes a worktree that contains a link.

On 2026-10-06 a cleanup ran `git worktree remove` on a worktree whose Data/Symbol and
Data/Symbol_full were directory junctions into the main checkout. `git status` called
the worktree clean (Data/ is gitignored), the removal followed the junctions, and the
real caches (551,974 files) were deleted. These tests build REAL junctions, but only
inside a throwaway temp dir pointing at a temp target with a sentinel file.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "utils"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import prune_merged_worktrees as prune


def _make_junction(link: Path, target: Path) -> bool:
    if sys.platform != "win32":
        os.symlink(target, link, target_is_directory=True)
        return True
    res = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
    return res.returncode == 0


class _TempTree(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="prune_links_"))
        self.target = self.root / "real_cache"
        self.target.mkdir()
        self.sentinel = self.target / "sentinel.json"
        self.sentinel.write_text("{}", encoding="utf-8")
        self.worktree = self.root / "wt"
        (self.worktree / "Data").mkdir(parents=True)
        self.link = self.worktree / "Data" / "Symbol"
        if not _make_junction(self.link, self.target):
            self.skipTest("cannot create a junction/symlink here")

    def tearDown(self):
        # Remove the LINK itself first (rmdir on a junction removes only the link),
        # then the rest; the sentinel in the target must survive that.
        for link in (getattr(self, "inner_link", None), self.link):
            if link is not None and os.path.lexists(link):
                os.rmdir(link) if sys.platform == "win32" else os.unlink(link)
        self.assertTrue(self.sentinel.exists(), "link cleanup must not touch the target")
        for p in sorted(self.root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            p.rmdir() if p.is_dir() else p.unlink()
        self.root.rmdir()


class TestLinksInside(_TempTree):
    def test_junction_is_found(self):
        found = prune.links_inside(str(self.worktree))
        self.assertEqual([Path(p) for p in found], [self.link])

    def test_scan_does_not_descend_into_the_link(self):
        # A second link INSIDE the target: a scan that followed the first link would
        # report two. links_inside must stop at the link, never walk through it.
        other = self.root / "other"
        other.mkdir()
        self.inner_link = self.target / "inner"
        if not _make_junction(self.inner_link, other):
            self.skipTest("cannot create a nested junction here")
        self.assertEqual([Path(p) for p in prune.links_inside(str(self.worktree))], [self.link])

    def test_clean_tree_has_no_links(self):
        os.rmdir(self.link) if sys.platform == "win32" else os.unlink(self.link)
        self.assertEqual(prune.links_inside(str(self.worktree)), [])


class TestJunctionFallback(_TempTree):
    def test_junction_found_even_without_os_path_isjunction(self):
        # Python < 3.12 has no os.path.isjunction, and is_symlink() is False for a
        # junction; the reparse-point attribute check must still catch it (fail closed).
        if sys.platform != "win32":
            self.skipTest("junctions are Windows-only")
        with mock.patch.object(prune.os.path, "isjunction", None, create=True):
            self.assertEqual([Path(p) for p in prune.links_inside(str(self.worktree))], [self.link])


class TestPlanRefusesLinkedWorktrees(_TempTree):
    def test_finished_worktree_with_a_link_is_kept_not_removed(self):
        wt = str(self.worktree)
        states = {"feat/x": {"number": 999, "merged": True, "state": "closed"}}
        with mock.patch.object(prune, "pr_state_map", return_value=states), \
             mock.patch.object(prune, "list_worktrees",
                               return_value=[{"path": wt, "branch": "feat/x", "detached": False}]), \
             mock.patch.object(prune, "current_branch", return_value="main"), \
             mock.patch.object(prune, "is_dirty", return_value=False), \
             mock.patch.object(prune, "_run", return_value=(0, "", "")):
            actions, keep = prune.plan(None, "o/r", want_merged=True, want_closed=False)
        self.assertEqual([a for a in actions if a[0] == "worktree"], [])
        reasons = [r for t, r in keep if t == wt]
        self.assertEqual(len(reasons), 1)
        self.assertIn("link", reasons[0].lower())
        self.assertIn(str(self.link), reasons[0])


class TestShrunk(unittest.TestCase):
    def test_a_vanished_cache_folder_counts_as_a_shrink(self):
        self.assertEqual(prune._shrunk({"Symbol": 500, "Symbol_full": 578},
                                       {"Symbol": None, "Symbol_full": 578}), ["Symbol"])

    def test_unchanged_or_growing_is_not_a_shrink(self):
        self.assertEqual(prune._shrunk({"Symbol": 500, "Symbol_full": None},
                                       {"Symbol": 510, "Symbol_full": None}), [])


class TestExecuteTripwire(unittest.TestCase):
    def test_stops_when_the_main_caches_shrink(self):
        counts = iter([{"Symbol": 500, "Symbol_full": 578}, {"Symbol": 0, "Symbol_full": 578}])
        removed = []

        def fake_run(cmd, check=True):
            if cmd[:3] == ["git", "worktree", "remove"]:
                removed.append(cmd[3])
            return 0, "", ""

        actions = [("worktree", "A", "feat/a", "r"), ("worktree", "B", "feat/b", "r")]
        with mock.patch.object(prune, "_run", side_effect=fake_run), \
             mock.patch.object(prune, "links_inside", return_value=[]), \
             mock.patch.object(prune, "main_cache_counts", side_effect=lambda: next(counts)):
            done, failed = prune.execute(actions)
        self.assertEqual(removed, ["A"])  # stopped before B
        self.assertTrue(any("cache" in str(f).lower() for f in failed))


if __name__ == "__main__":
    unittest.main()
