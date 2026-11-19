"""aether/drop_risk.py: how prone a stock is to sudden drops, from its own history (advisory).

PROD 2026-11-18/19: SA was recommended and fell ~30% the next day. Its 3-year history (5 days of -10%
or worse, worst 1% of days -9.7%) set it apart from the median stock (2 days, -6.1%). The Oracle now
shows that, and a size hint like the game's 1% risk cap. Cached bar series hold junk (RS: holiday
bars at 369.29 between ~268-350 closes), which must not count as drops.
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

_REPO = Path(__file__).resolve().parent.parent


def _sessions(n, start=datetime.date(2024, 1, 2)):
    out, d = [], start
    while len(out) < n:
        if dr.market_calendar.is_session(d):
            out.append(d.isoformat())
        d += datetime.timedelta(days=1)
    return out


def _bars(closes, dates=None):
    dates = dates or _sessions(len(closes))
    return mock.patch.object(dr.risk_utils, "_load_ohlcv_bars", return_value=(dates, closes, closes, closes))


class ProfileTest(unittest.TestCase):
    def test_counts_real_drops(self):
        closes = [100.0] * 300
        for i in (100, 150, 200):                       # three -12% days that stay down
            closes[i:] = [c * 0.88 for c in closes[i:]]
        with _bars(closes):
            p = dr.profile("X")
        self.assertEqual(p["drops"], 3)
        self.assertAlmostEqual(p["worst"], -0.12, places=6)

    def test_a_plateau_spike_is_not_a_drop(self):
        closes = [100.0] * 300
        closes[120] = closes[121] = 138.0               # the RS shape: two junk bars, then back
        with _bars(closes):
            p = dr.profile("X")
        self.assertEqual(p["drops"], 0)
        self.assertAlmostEqual(p["worst"], 0.0)
        self.assertEqual(dr.drop_spikes([100, 138, 138, 101, 100]), [100, 101, 100])
        self.assertEqual(dr.drop_spikes([100, 70, 69, 68]), [100, 70, 69, 68])   # a crash that stays down is kept

    def test_bars_on_closed_days_are_dropped(self):
        dates = sorted(_sessions(299) + ["2024-07-04"])  # Independence Day: NYSE closed
        closes = [100.0] * 300
        closes[dates.index("2024-07-04")] = 105.0          # near the others: only the holiday rule removes it
        with _bars(closes, dates):
            p = dr.profile("X")
        self.assertEqual(p["sessions"], 298)            # 299 session closes -> 298 returns
        self.assertEqual(p["drops"], 0)

    def test_too_little_history(self):
        with _bars([100.0] * 100):
            self.assertIsNone(dr.profile("X"))


class NoteAndSizeTest(unittest.TestCase):
    def test_note(self):
        sa = {"sessions": 756, "drops": 5, "p1": -0.097, "worst": -0.157}
        self.assertEqual(dr.note(sa), "high drop risk: 5 days of -10% or worse in 3 years; worst 1% of days -9.7%")
        self.assertEqual(dr.note({"sessions": 756, "drops": 1, "p1": -0.05, "worst": -0.1}), "")
        self.assertIn("worst 1% of days -8.5%", dr.note({"sessions": 756, "drops": 0, "p1": -0.085, "worst": -0.1}))
        self.assertEqual(dr.note(None), "")

    def test_size_hint_matches_the_games_cap_for_sa(self):
        """11-18: the game's 1% risk cap sized SA at 17 shares ($26.03, stop $20.43, equity ~$10k)."""
        sa = {"p1": -0.097}
        self.assertEqual(dr.size_hint(26.03, 20.43, 10002.9, sa), 17)
        self.assertEqual(dr.size_hint(100.0, 99.0, 10000.0, {"p1": -0.10}), 10)   # worst-1% day binds, not the stop
        self.assertIsNone(dr.size_hint(0, 1, 10000, sa))
        self.assertIsNone(dr.size_hint(10.0, 0.0, 10000, None))


class WiredTest(unittest.TestCase):
    def test_the_oracle_card_shows_the_risk_and_the_size_hint(self):
        acct = {"id": "9999", "balance": 100.0, "equity": 20000.0, "holdings": []}
        buys = [{"symbol": "SA", "price": 26.03, "stop": 20.43, "target": 26.76, "combined": 14.1, "pgr": "Be",
                 "patterns": ""}]
        prof = {"sessions": 756, "drops": 5, "p1": -0.097, "worst": -0.157}
        with mock.patch.object(oracle.drop_risk, "profile", return_value=prof), \
             mock.patch.object(oracle.earnings_calendar, "load", return_value={}):
            html = oracle.generate_oracle_html(acct, [], [], buys)
        self.assertIn("high drop risk: 5 days of -10% or worse in 3 years", html)
        self.assertIn("Size hint: up to 35 shares", html)

    def test_the_evening_email_shows_the_risk(self):
        src = (_REPO / "daily_task.py").read_text(encoding="utf-8")
        body = src[src.index("def add_rows("):src.index("def list_table(")]
        self.assertIn("drop_risk.note(drop_risk.profile(r['symbol']))", body)


if __name__ == "__main__":
    unittest.main()
