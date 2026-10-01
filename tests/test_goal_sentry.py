"""
Tests for scripts/utils/goal_sentry.py — the Oracle audit's E*TRADE gate.

run_sentry() must ensure the session through the unattended re-auth door
etrade.scheduled_reauth("production") (renew-first, headless mint) and only read live
account equity when that door reports a live session; otherwise it falls back to the
configured start equity. It must never call get_tokens(), whose default mint is headful
and cannot launch on a display-less PROD host.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "utils"))
import goal_sentry as gs


class TestOracleAuditGate(unittest.TestCase):
    START = 20000.0
    LIVE = 25000.0

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.target = Path(self.tmp.name) / "Data" / "active_targets.json"
        for p in (mock.patch.object(gs, "BASE_DIR", Path(self.tmp.name)),   # no game file -> Project 1 skipped
                  mock.patch.object(gs, "TARGET_PATH", str(self.target)),
                  mock.patch.object(gs.CFG, "oracle_account", "ACCT-1", create=True),
                  mock.patch.object(gs.CFG, "oracle_start_equity", self.START, create=True),
                  mock.patch.object(gs.CFG, "oracle_target_date", "2099-12-31", create=True),
                  mock.patch.object(gs.etrade, "get_tokens",
                                    side_effect=AssertionError("must use scheduled_reauth"))):
            p.start()
            self.addCleanup(p.stop)

    def test_live_equity_read_only_when_the_door_reports_a_live_session(self):
        accounts = {"accounts": [{"id": "OTHER", "equity": 1.0}, {"id": "ACCT-1", "equity": self.LIVE}]}
        cases = [
            # scheduled_reauth result,              read_accounts called, expected current_equity
            ({"ok": True,  "reason": "renewed"},     True,  self.LIVE),
            ({"ok": True,  "reason": "reauthed"},    True,  self.LIVE),
            ({"ok": False, "reason": "failed"},      False, self.START),
            ({"ok": False, "reason": "blocked"},     False, self.START),
        ]
        for result, reads, expected in cases:
            with self.subTest(**result), \
                 mock.patch.object(gs.etrade, "scheduled_reauth", return_value=result) as m_door, \
                 mock.patch.object(gs.data_api, "read_accounts", return_value=accounts) as m_read:
                gs.run_sentry()
                oracle = json.loads(self.target.read_text())["oracle_project"]
                self.assertNotIn("error", oracle)
                self.assertEqual(oracle["current_equity"], expected)
                m_door.assert_called_once_with("production")
                self.assertEqual(m_read.called, reads)


if __name__ == "__main__":
    unittest.main()
