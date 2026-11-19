"""aether/drop_risk.py: how prone a stock is to sudden drops, from its own history (advisory).

PROD 2026-11-18/19: SA was recommended and fell ~30% the next day. R&D #115 found that admitted buys
with 4+ drop days in the prior 756 sessions hit a -15% worst close within 10 sessions 8.9% of the
time vs 1.2% (HAC t 8.89). The flag shows that same count, through the one shared definition
``risk_utils.drop_day_flags`` (review of #299: one definition for the flag and the study).
"""
import datetime
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tests  # noqa: F401  hermetic harness
import aether_oracle as oracle
from aether import drop_risk as dr
from aether import risk_utils as ru

_REPO = Path(__file__).resolve().parent.parent


def _sessions(n, start=datetime.date(2023, 1, 3)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += datetime.timedelta(days=1)
    return out


class DropDayFlagsTest(unittest.TestCase):
    def test_the_study_rule(self):
        d = _sessions(6)
        #            -12% (stays)  -15% then +20% bounce (bad bar)
        closes = [100, 88, 88, 74.8, 89.76, 89.76]
        self.assertEqual(ru.drop_day_flags(d, closes), [False, True, False, False, False, False])

    def test_a_return_across_a_session_gap_is_left_out(self):
        d = ["2024-01-02", "2024-01-03", "2024-01-25", "2024-01-26"]   # 22-day hole before the 3rd bar
        self.assertEqual(ru.drop_day_flags(d, [100, 100, 80, 80]), [False, False, False, False])
        self.assertEqual(ru.daily_returns(d, [100, 100, 80, 80]), [0.0, None, 0.0])


class ProfileTest(unittest.TestCase):
    def _profile(self, closes):
        d = _sessions(len(closes))
        with mock.patch.object(dr.risk_utils, "_load_ohlcv_bars", return_value=(d, closes, closes, closes)):
            return dr.profile("X")

    def test_counts_drops_in_the_756_sessions_before_the_decision_day(self):
        closes = [100.0] * 800
        for i in (100, 300, 500, 799):                 # 799 is the decision day itself: not counted
            closes[i:] = [c * 0.88 for c in closes[i:]]
        p = self._profile(closes)
        self.assertEqual(p["sessions"], 756)
        self.assertEqual(p["drops"], 3)
        closes2 = [100.0] * 800
        closes2[10:] = [c * 0.88 for c in closes2[10:]]   # before the window (i < 799-756 = 43)
        self.assertEqual(self._profile(closes2)["drops"], 0)

    def test_too_little_history(self):
        self.assertIsNone(self._profile([100.0] * 756))


class NoteAndSizeTest(unittest.TestCase):
    def test_note_follows_the_tested_count_only(self):
        sa = {"sessions": 756, "drops": 5, "p1": -0.097, "worst": -0.157}
        self.assertEqual(dr.note(sa), "high drop risk: 5 days of -10% or worse in 3 years; worst 1% of days -9.7%")
        self.assertEqual(dr.note(dict(sa, drops=3)), "")
        self.assertEqual(dr.note({"sessions": 756, "drops": 0, "p1": -0.15, "worst": -0.2}), "")  # p1 alone: untested
        self.assertEqual(dr.note(None), "")

    def test_size_hint_uses_the_games_risk_cap(self):
        """11-18: the game's 1% risk cap sized SA at 17 shares ($26.03, stop $20.43, equity ~$10k)."""
        sa = {"p1": -0.097}
        with mock.patch.object(dr.CFG, "system_max_trade_risk_pct", 0.01):
            self.assertEqual(dr.size_hint(26.03, 20.43, 10002.9, sa), 17)
            self.assertEqual(dr.size_hint(100.0, 99.0, 10000.0, {"p1": -0.10}), 10)   # the worst-1% day binds
        with mock.patch.object(dr.CFG, "system_max_trade_risk_pct", 0.02):
            self.assertEqual(dr.size_hint(26.03, 20.43, 10002.9, sa), 35)            # follows the config
        self.assertIsNone(dr.size_hint(0, 1, 10000, sa))
        self.assertIsNone(dr.size_hint(10.0, 0.0, 10000, None))


class WiredTest(unittest.TestCase):
    def test_the_oracle_card_shows_the_risk_and_the_size_hint(self):
        acct = {"id": "9999", "balance": 100.0, "equity": 20000.0, "holdings": []}
        buys = [{"symbol": "SA", "price": 26.03, "stop": 20.43, "target": 26.76, "combined": 14.1, "pgr": "Be",
                 "s10": 6.9, "l60": 8.7, "patterns": ""}]
        prof = {"sessions": 756, "drops": 5, "p1": -0.097, "worst": -0.157}
        with mock.patch.object(oracle.drop_risk, "profile", return_value=prof), \
             mock.patch.object(oracle.earnings_calendar, "load", return_value={}), \
             mock.patch.object(dr.CFG, "system_max_trade_risk_pct", 0.01):
            html = oracle.generate_oracle_html(acct, [], [], buys)
        self.assertIn("high drop risk: 5 days of -10% or worse in 3 years", html)
        self.assertIn("Size hint: up to 35 shares", html)

    def test_the_evening_email_shows_the_risk(self):
        src = (_REPO / "daily_task.py").read_text(encoding="utf-8")
        body = src[src.index("def add_rows("):src.index("def list_table(")]
        self.assertIn("drop_risk.note(drop_risk.profile(r['symbol']))", body)


if __name__ == "__main__":
    unittest.main()
