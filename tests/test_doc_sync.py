"""
Tests for the pre-commit feature-doc-sync guard
(scripts/utils/pre_commit_validator.py :: check_feature_doc_sync).

These exercise the REAL shipped function with only git access monkeypatched, so a
regression in the guard's decision logic (region overlap, ack bypass, surface
mapping) fails here rather than silently letting docs drift. No git, no network.
"""
import contextlib
import importlib.util
import io
import os
import unittest
from unittest import mock


# The validator lives under scripts/utils, which is deliberately NOT a package,
# so load it by path (conftest.py has already put the repo root on sys.path).
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_VALIDATOR_PATH = os.path.join(_ROOT, "scripts", "utils", "pre_commit_validator.py")
_spec = importlib.util.spec_from_file_location("pre_commit_validator", _VALIDATOR_PATH)
pcv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pcv)

# Synthetic anchored source, so the tests do not depend on the production
# scarcity_core line numbers (which shift as ai_portfolio_game.py evolves).
# The 'testfeat' anchor region spans lines 4..7 (1-indexed).
SRC = "\n".join([
    "def foo():",                   # 1
    "    return 1",                 # 2
    "",                             # 3
    "# @doc-sync-start: testfeat",  # 4
    "def bar():",                   # 5
    "    return 2",                 # 6
    "# @doc-sync-end: testfeat",    # 7
    "",                             # 8
    "def baz():",                   # 9
    "    return 3",                 # 10
]) + "\n"

DOC = "docs/testfeat.md"

# Diff hunks (git diff --cached -U0 headers) targeting specific new-file lines.
HUNK_INSIDE = "@@ -5,1 +5,1 @@\n-    return 2\n+    return 22\n"   # line 5, inside 4..7
HUNK_OUTSIDE = "@@ -10,1 +10,1 @@\n-    return 3\n+    return 33\n"  # line 10, outside
HUNK_START_EDGE = "@@ -4,1 +4,1 @@\n-x\n+x \n"                       # line 4, region start


def _fake_git(staged_names, content_by_path, diff_by_path):
    """Build a fake _git_stdout that dispatches on the git argv it receives."""
    def _inner(args):
        if "--name-only" in args:
            return "\n".join(staged_names) + "\n"
        if args and args[0] == "show":
            return content_by_path.get(args[1][1:])  # strip the leading ':'
        if "-U0" in args:
            return diff_by_path.get(args[-1], "")
        return ""
    return _inner


class DocSyncGuardTest(unittest.TestCase):
    def setUp(self):
        os.environ.pop("AETHER_DOCSYNC_ACK", None)
        # Map the synthetic feature to a synthetic surface (decoupled from prod config).
        self._surfaces = mock.patch.object(
            pcv, "DOC_SYNC_SURFACES", {"testfeat": [(DOC, "the testfeat doc")]})
        self._surfaces.start()
        self.addCleanup(self._surfaces.stop)
        self.addCleanup(lambda: os.environ.pop("AETHER_DOCSYNC_ACK", None))

    def _run(self, staged, diffs):
        fake = _fake_git(staged, {"src.py": SRC}, diffs)
        with mock.patch.object(pcv, "_git_stdout", fake), \
             contextlib.redirect_stdout(io.StringIO()):
            return pcv.check_feature_doc_sync()

    def test_blocks_when_anchored_code_changed_without_doc(self):
        # RED: guard must return False (commit blocked) when the mapped doc is absent.
        self.assertFalse(self._run(["src.py"], {"src.py": HUNK_INSIDE}))

    def test_passes_when_doc_staged(self):
        # GREEN: same code change, but the doc surface is staged too.
        self.assertTrue(self._run(["src.py", DOC], {"src.py": HUNK_INSIDE}))

    def test_ack_env_bypasses(self):
        os.environ["AETHER_DOCSYNC_ACK"] = "testfeat"
        self.assertTrue(self._run(["src.py"], {"src.py": HUNK_INSIDE}))

    def test_ack_for_other_key_does_not_bypass(self):
        # Ack is scoped: acking a different feature must NOT unblock this one.
        os.environ["AETHER_DOCSYNC_ACK"] = "somethingelse"
        self.assertFalse(self._run(["src.py"], {"src.py": HUNK_INSIDE}))

    def test_change_outside_region_is_ignored(self):
        # Editing the same file outside any anchor must not demand a doc update.
        self.assertTrue(self._run(["src.py"], {"src.py": HUNK_OUTSIDE}))

    def test_region_boundary_start_line_is_a_hit(self):
        # Overlap is inclusive: a change on the region's first line counts.
        self.assertFalse(self._run(["src.py"], {"src.py": HUNK_START_EDGE}))

    def test_no_staged_files_passes(self):
        with mock.patch.object(pcv, "_git_stdout", _fake_git([], {}, {})), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(pcv.check_feature_doc_sync())


class AnchorParseTest(unittest.TestCase):
    def test_parses_regions_and_drops_unclosed(self):
        content = "\n".join([
            "a",                          # 1
            "# @doc-sync-start: k1",      # 2
            "b",                          # 3
            "# @doc-sync-end: k1",        # 4
            "# @doc-sync-start: orphan",  # 5  (never closed -> dropped)
            "c",                          # 6
        ])
        self.assertEqual(pcv._anchor_regions(content), {"k1": [(2, 4)]})

    def test_multiple_regions_same_key(self):
        content = "\n".join([
            "# @doc-sync-start: k",   # 1
            "x",                      # 2
            "# @doc-sync-end: k",     # 3
            "y",                      # 4
            "# @doc-sync-start: k",   # 5
            "z",                      # 6
            "# @doc-sync-end: k",     # 7
        ])
        self.assertEqual(pcv._anchor_regions(content), {"k": [(1, 3), (5, 7)]})


if __name__ == "__main__":
    unittest.main()
