"""aether.market_calendar: the one NYSE holiday list and the last-session lookup."""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ai_portfolio_game as game  # noqa: E402
from aether import market_calendar as cal  # noqa: E402

D = datetime.date.fromisoformat


class TestLastSession(unittest.TestCase):
    def test_last_session_on_or_before(self):
        for day, want in (("2026-12-24", "2026-12-24"),   # a session is itself
                          ("2026-12-25", "2026-12-24"),   # Friday holiday -> Thursday
                          ("2026-12-27", "2026-12-24"),   # Sunday after it
                          ("2027-01-01", "2026-12-31"),   # New Year's Day (Friday)
                          ("2026-09-07", "2026-09-04")):  # Monday holiday -> previous Friday
            with self.subTest(day=day):
                self.assertEqual(cal.last_session_on_or_before(D(day)), D(want))

    def test_the_game_reexports_the_same_list(self):
        self.assertIs(game.NYSE_HOLIDAYS, cal.NYSE_HOLIDAYS)


if __name__ == "__main__":
    unittest.main()
