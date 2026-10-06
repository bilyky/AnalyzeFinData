"""
Tests for scripts/utils/prune_merged_worktrees.py — PR-state classification.

The pulls LIST endpoint (``pulls?state=...``) does NOT return a ``merged`` field; merged-ness
is only visible as ``merged_at``. The fixtures below mirror that real payload shape (no
``merged`` key), so the classification is exercised exactly as GitHub returns it. Only the
subprocess boundary (``_run`` -> ``gh api``) is mocked; pr_state_map and _finished run for real.
"""
import io
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "utils"))
import prune_merged_worktrees as pmw


def _pr(number, ref, state, merged_at):
    # Shape of a pulls LIST row: note there is no "merged" key.
    return {"number": number, "state": state, "merged_at": merged_at, "head": {"ref": ref}}


# Newest first, as the API returns them with sort=created&direction=desc. With the page size
# patched to 2 below, CLOSED spans two FULL pages (+ an empty third) and OPEN one full page.
CLOSED = [
    _pr(140, "fix/merged-branch", "closed", "2026-09-30T19:29:45Z"),
    _pr(108, "feat/abandoned", "closed", None),
    _pr(61, "feat/reused", "closed", "2026-09-10T00:00:00Z"),       # page 2
    _pr(30, "fix/merged-branch", "closed", None),                  # page 2: OLDER PR, same ref as #140
    _pr(12, "feat/only-on-page-3", "closed", "2026-01-05T00:00:00Z"),   # page 3 (short page -> stop)
]
OPEN = [
    _pr(132, "feat/open-branch", "open", None),
    _pr(96, "feat/reused", "open", None),          # same head ref as closed #61
]
PAGE = 2


def _fake_run(cmd, check=True):
    """A paginated pulls-list endpoint: honours per_page / page in the requested path."""
    path = cmd[2]
    q = dict(kv.split("=", 1) for kv in path.split("?", 1)[1].split("&"))
    rows = CLOSED if q["state"] == "closed" else OPEN
    size, page = int(q["per_page"]), int(q["page"])
    return 0, json.dumps(rows[(page - 1) * size: page * size]), ""


class TestPrStateClassification(unittest.TestCase):
    def setUp(self):
        for p in (mock.patch.object(pmw, "_run", side_effect=_fake_run),
                  mock.patch.object(pmw, "_PAGE_SIZE", PAGE)):
            m = p.start()
            self.addCleanup(p.stop)
            if p.attribute == "_run":
                self.m_run = m
        self.state = pmw.pr_state_map("gh", "owner/repo")

    def test_merged_at_decides_merged_vs_closed_unmerged(self):
        cases = [
            # head ref,            number, state,    merged, --merged-only, --closed-only
            ("fix/merged-branch",  140,    "closed", True,   True,          False),
            ("feat/abandoned",     108,    "closed", False,  False,         True),
            ("feat/open-branch",   132,    "open",   False,  False,         False),
        ]
        for ref, number, st, merged, merged_only, closed_only in cases:
            with self.subTest(ref=ref):
                info = self.state[ref]
                self.assertEqual((info["number"], info["state"], info["merged"]), (number, st, merged))
                self.assertEqual(pmw._finished(info, want_merged=True, want_closed=False), merged_only)
                self.assertEqual(pmw._finished(info, want_merged=False, want_closed=True), closed_only)
                self.assertTrue(pmw._finished(info, True, True) or st == "open")   # default mode

    def test_open_dominates_a_reused_head_ref(self):
        info = self.state["feat/reused"]
        self.assertEqual((info["number"], info["state"], info["merged"]), (96, "open", False))
        self.assertFalse(pmw._finished(info, want_merged=True, want_closed=True))   # never pruned

    def test_pr_beyond_the_first_page_is_found(self):
        # #12's branch exists ONLY on page 3 of the closed list; one page per scope never saw it.
        info = self.state["feat/only-on-page-3"]
        self.assertEqual((info["number"], info["state"], info["merged"]), (12, "closed", True))
        self.assertTrue(pmw._finished(info, want_merged=True, want_closed=False))

    def test_newest_pr_wins_a_reused_ref_within_a_scope(self):
        # Older abandoned #30 (page 2) must not relabel the branch whose newest PR #140 merged.
        info = self.state["fix/merged-branch"]
        self.assertEqual((info["number"], info["merged"]), (140, True))

    def test_queries_every_page_newest_first_open_last_without_jq(self):
        paths = [c.args[0][2] for c in self.m_run.call_args_list]
        base = "repos/owner/repo/pulls?state=%s&sort=created&direction=desc&per_page=2&page=%d"
        self.assertEqual(paths, [base % ("closed", 1), base % ("closed", 2), base % ("closed", 3),
                                 base % ("open", 1), base % ("open", 2)])   # open queried LAST
        self.assertTrue(all("--jq" not in c.args[0] for c in self.m_run.call_args_list))

    def test_runaway_pagination_is_refused(self):
        with mock.patch.object(pmw, "_MAX_PAGES", 1), self.assertRaises(RuntimeError):
            pmw.pr_state_map("gh", "owner/repo")          # page 1 of closed is full, cap is 1


class TestMainFailsCleanly(unittest.TestCase):
    def test_unreachable_gh_exits_2_with_one_actionable_message(self):
        err = io.StringIO()
        boom = RuntimeError("command failed (1): gh api repos/o/r/pulls\ndial tcp 1.2.3.4:443: connectex")
        with mock.patch.object(pmw, "_resolve_gh", return_value="gh"), \
             mock.patch.object(pmw, "_run", side_effect=boom), \
             mock.patch.object(pmw, "execute") as m_exec, \
             mock.patch.object(sys, "stderr", err), \
             mock.patch.object(sys, "stdout", io.StringIO()):
            rc = pmw.main(["--apply"])
        self.assertEqual(rc, 2)
        msg = err.getvalue()
        self.assertIn("ERROR: could not plan the prune: command failed (1): gh api repos/o/r/pulls", msg)
        self.assertIn("Nothing was changed", msg)
        self.assertIn("HTTPS_PROXY", msg)
        self.assertNotIn("dial tcp", msg)                 # first line only, no traceback dump
        m_exec.assert_not_called()                        # --apply never reaches execution


if __name__ == "__main__":
    unittest.main()
