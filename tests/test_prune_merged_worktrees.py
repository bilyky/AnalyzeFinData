"""
Tests for scripts/utils/prune_merged_worktrees.py — PR-state classification.

The pulls LIST endpoint (``pulls?state=...``) does NOT return a ``merged`` field; merged-ness
is only visible as ``merged_at``. The fixtures below mirror that real payload shape (no
``merged`` key), so the classification is exercised exactly as GitHub returns it. Only the
subprocess boundary (``_run`` -> ``gh api``) is mocked; pr_state_map and _finished run for real.
"""
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


CLOSED = [
    _pr(140, "fix/merged-branch", "closed", "2026-09-30T19:29:45Z"),
    _pr(108, "feat/abandoned", "closed", None),
    _pr(61, "feat/reused", "closed", "2026-09-10T00:00:00Z"),
]
OPEN = [
    _pr(132, "feat/open-branch", "open", None),
    _pr(96, "feat/reused", "open", None),          # same head ref as closed #61
]


def _fake_run(cmd, check=True):
    path = cmd[2]
    body = CLOSED if "state=closed" in path else OPEN
    return 0, json.dumps(body), ""


class TestPrStateClassification(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(pmw, "_run", side_effect=_fake_run)
        self.m_run = p.start()
        self.addCleanup(p.stop)
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

    def test_queries_the_list_endpoint_without_jq(self):
        paths = [c.args[0] for c in self.m_run.call_args_list]
        self.assertEqual([p[2] for p in paths],
                         ["repos/owner/repo/pulls?state=closed&per_page=100",
                          "repos/owner/repo/pulls?state=open&per_page=100"])   # open queried LAST
        self.assertTrue(all("--jq" not in p for p in paths))


if __name__ == "__main__":
    unittest.main()
