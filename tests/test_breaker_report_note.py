"""The breaker's verdict reaches the daily summary email (owner 2026-11-25).

The 11-25 email said "Active Strategy: AGGRESSIVE" while the breaker had frozen buys and tightened stops (SPY 6.95%
under its 10-day peak). The breaker now saves one record per run on the game state (RECORD_KEY) and the email shows
it next to the profile, which is labelled with its source.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ai_portfolio_game as game  # noqa: E402
from aether import circuit_breaker  # noqa: E402

REASON = "Rolling 10-day Drawdown Breach (SPY is down -6.95% from its 10-day peak)."


class TestRecord(unittest.TestCase):
    def _enforce(self, active, state):
        with mock.patch.object(circuit_breaker, "check_systemic_risk", return_value=(active, REASON if active else "")), \
             mock.patch.object(circuit_breaker, "log_circuit_breaker_trigger_dna"), \
             mock.patch.object(circuit_breaker, "_today_et", return_value="2026-11-25"), \
             mock.patch.object(circuit_breaker.risk_utils, "calculate_atr", return_value=2.0):
            return circuit_breaker.enforce_circuit_breaker(state, prices={"ON": 62.0, "PCG": 14.0})

    def test_on_records_reason_and_tightened(self):
        state = {"positions": {"ON": {"stop_loss": 57.65, "cost": 73.0},
                               "PCG": {"stop_loss": 13.0, "cost": 13.7, "is_scarcity": True}}}
        self._enforce(True, state)
        self.assertEqual(state[circuit_breaker.RECORD_KEY],
                         {"date": "2026-11-25", "on": True, "reason": REASON, "tightened": ["ON"]})

    def test_off_overwrites_the_record(self):
        state = {"positions": {}, circuit_breaker.RECORD_KEY: {"date": "2026-11-24", "on": True}}
        self._enforce(False, state)
        self.assertEqual(state[circuit_breaker.RECORD_KEY],
                         {"date": "2026-11-25", "on": False, "reason": None, "tightened": []})


class TestNote(unittest.TestCase):
    def test_note_only_for_today_and_on(self):
        rec = {"date": "2026-11-25", "on": True, "reason": REASON, "tightened": ["SWKS", "PCG"]}
        note = circuit_breaker.breaker_note({circuit_breaker.RECORD_KEY: rec}, today="2026-11-25")
        self.assertIn("Circuit breaker ON this run", note)
        self.assertIn("-6.95%", note)
        self.assertIn("stops tightened to 1x ATR on SWKS, PCG", note)
        self.assertIsNone(circuit_breaker.breaker_note({circuit_breaker.RECORD_KEY: rec}, today="2026-11-26"))
        self.assertIsNone(circuit_breaker.breaker_note({circuit_breaker.RECORD_KEY: {**rec, "on": False}},
                                                       today="2026-11-25"))
        self.assertIsNone(circuit_breaker.breaker_note({}, today="2026-11-25"))
        no_tight = circuit_breaker.breaker_note({circuit_breaker.RECORD_KEY: {**rec, "tightened": []}}, today="2026-11-25")
        self.assertNotIn("stops tightened", no_tight)


class TestEmail(unittest.TestCase):
    def _html(self, record):
        state = {"balance": 1000.0, "positions": {"AAA": {"qty": 10, "cost": 5.0}}, "equity": 1050.0,
                 "history": [], "start_date": "2026-01-01", "profile": "AGGRESSIVE"}
        if record:
            state[circuit_breaker.RECORD_KEY] = record
        with mock.patch.object(game, "load_game", return_value=state), \
             mock.patch.object(game, "get_live_prices", return_value={"AAA": 5.0}), \
             mock.patch.object(game, "save_game"), \
             mock.patch.object(game.openpyxl, "load_workbook", side_effect=Exception("no wb")), \
             mock.patch.object(circuit_breaker, "_today_et", return_value="2026-11-25"):
            return game.send_daily_summary(return_html=True)

    def test_email_shows_the_breaker_and_labels_the_profile(self):
        html = self._html({"date": "2026-11-25", "on": True, "reason": REASON, "tightened": ["ON"]})
        self.assertIn("Circuit breaker ON this run", html)
        self.assertIn("Profile</b> (from the morning SPY Long60 score)", html)
        self.assertNotIn("Active Strategy", html)

    def test_no_breaker_line_when_off_or_stale(self):
        self.assertNotIn("Circuit breaker ON", self._html({"date": "2026-11-24", "on": True, "reason": REASON}))
        self.assertNotIn("Circuit breaker ON", self._html(None))


if __name__ == "__main__":
    unittest.main()
