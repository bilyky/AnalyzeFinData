"""The breaker's verdict reaches the daily summary email (owner 2026-11-25).

The 11-25 email said "Active Strategy: AGGRESSIVE" while the breaker had frozen buys and tightened stops (SPY 6.95%
under its 10-day peak). The breaker now writes one record per run to its own file (ledgers.BREAKER_LAST_RUN_FILE,
not the game state: #326 review) and the email shows it next to the profile, which is labelled with its source.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ai_portfolio_game as game  # noqa: E402
from aether import circuit_breaker, ledgers  # noqa: E402

REASON = "Rolling 10-day Drawdown Breach (SPY is down -6.95% from its 10-day peak)."


class _RecordFile(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "breaker_last_run.json"
        p = mock.patch.object(ledgers, "BREAKER_LAST_RUN_FILE", self.path)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self._tmp.cleanup)

    def write(self, record):
        self.path.write_text(json.dumps(record), encoding="utf-8")


class TestRecord(_RecordFile):
    def _enforce(self, active, state):
        with mock.patch.object(circuit_breaker, "check_systemic_risk", return_value=(active, REASON if active else "")), \
             mock.patch.object(circuit_breaker, "log_circuit_breaker_trigger_dna"), \
             mock.patch.object(circuit_breaker, "_today_et", return_value="2026-11-25"), \
             mock.patch.object(circuit_breaker.risk_utils, "calculate_atr", return_value=2.0):
            return circuit_breaker.enforce_circuit_breaker(state, prices={"ON": 62.0, "PCG": 14.0})

    def test_on_writes_reason_and_tightened_to_its_own_file(self):
        state = {"positions": {"ON": {"stop_loss": 57.65, "cost": 73.0},
                               "PCG": {"stop_loss": 13.0, "cost": 13.7, "is_scarcity": True}}}
        self._enforce(True, state)
        self.assertEqual(circuit_breaker.load_record(),
                         {"date": "2026-11-25", "on": True, "reason": REASON, "tightened": ["ON"]})
        self.assertNotIn("breaker_last_run", state)          # nothing of the breaker's in the portfolio state

    def test_off_overwrites_the_record(self):
        self.write({"date": "2026-11-24", "on": True, "reason": REASON, "tightened": ["ON"]})
        self._enforce(False, {"positions": {}})
        self.assertEqual(circuit_breaker.load_record(),
                         {"date": "2026-11-25", "on": False, "reason": None, "tightened": []})

    def test_unreadable_file_reads_as_no_record(self):
        self.path.write_text("not json", encoding="utf-8")
        self.assertEqual(circuit_breaker.load_record(), {})


class TestNote(_RecordFile):
    def test_note_only_for_today_and_on(self):
        rec = {"date": "2026-11-25", "on": True, "reason": REASON, "tightened": ["SWKS", "PCG"]}
        self.write(rec)
        note = circuit_breaker.breaker_note(today="2026-11-25")
        self.assertIn("Circuit breaker ON this run", note)
        self.assertIn("-6.95%", note)
        self.assertIn("stops tightened to 1x ATR on SWKS, PCG", note)
        self.assertIsNone(circuit_breaker.breaker_note(today="2026-11-26"))
        self.write({**rec, "on": False})
        self.assertIsNone(circuit_breaker.breaker_note(today="2026-11-25"))
        self.write({**rec, "tightened": []})
        self.assertNotIn("stops tightened", circuit_breaker.breaker_note(today="2026-11-25"))
        self.path.unlink()
        self.assertIsNone(circuit_breaker.breaker_note(today="2026-11-25"))


class TestEmail(unittest.TestCase):
    """Patches the breaker module object the game holds (``game.circuit_breaker``) directly. On CI (77b3bf9) the
    file-based patch did not reach the email in the full suite's order, while it passed alone and locally; the cause
    was not pinned down, so this test no longer depends on it."""

    def _html(self, record):
        state = {"balance": 1000.0, "positions": {"AAA": {"qty": 10, "cost": 5.0}}, "equity": 1050.0,
                 "history": [], "start_date": "2026-01-01", "profile": "AGGRESSIVE"}
        with mock.patch.object(game, "load_game", return_value=state), \
             mock.patch.object(game, "get_live_prices", return_value={"AAA": 5.0}), \
             mock.patch.object(game, "save_game"), \
             mock.patch.object(game.openpyxl, "load_workbook", side_effect=Exception("no wb")), \
             mock.patch.object(game.circuit_breaker, "load_record", return_value=record),              mock.patch.object(game.circuit_breaker, "_today_et", return_value="2026-11-25"):
            return game.send_daily_summary(return_html=True)

    def test_email_shows_the_breaker_and_labels_the_profile(self):
        html = self._html({"date": "2026-11-25", "on": True, "reason": REASON, "tightened": ["ON"]})
        self.assertIn("Circuit breaker ON this run", html)
        self.assertIn("Profile</b> (from the morning SPY Long60 score)", html)
        self.assertNotIn("Active Strategy", html)

    def test_no_breaker_line_when_stale_or_missing(self):
        self.assertNotIn("Circuit breaker ON", self._html({"date": "2026-11-24", "on": True, "reason": REASON}))
        self.assertNotIn("Circuit breaker ON", self._html({}))


if __name__ == "__main__":
    unittest.main()
