"""
Unit tests for is_market_hours in ai_portfolio_game to verify weekend/holiday bypass of E*TRADE token fetches.
"""
import datetime
import os
import sys
import unittest
from unittest import mock

import pytz

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ai_portfolio_game as game


class TestGameMarketHours(unittest.TestCase):

    def test_weekend_skips_etrade_token_fetch(self):
        """On weekends, is_market_hours should return False without fetching E*TRADE tokens."""
        # Set up datetime to return a Sunday morning during typical market hours (e.g. 7:00 AM PST / 10:00 AM EST)
        tz_la = pytz.timezone("America/Los_Angeles")
        # Sunday, August 16, 2026, 07:00:00 PST
        sunday_dt = tz_la.localize(datetime.datetime(2026, 8, 16, 7, 0, 0))

        # We will mock datetime.datetime to return our specific Sunday
        class MockDatetime(datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                if tz is not None:
                    return sunday_dt.astimezone(tz)
                return sunday_dt

        # Mock E*TRADE get_tokens. If it gets called, we fail/raise exception.
        m_get_tokens = mock.patch("ai_portfolio_game.etrade.get_tokens")
        mock_get_tokens = m_get_tokens.start()
        self.addCleanup(m_get_tokens.stop)

        with mock.patch("ai_portfolio_game.datetime.datetime", MockDatetime):
            is_hours = game.is_market_hours()

        # It should return False because it's a weekend
        self.assertFalse(is_hours)
        # E*TRADE get_tokens should NOT have been called
        mock_get_tokens.assert_not_called()

    def test_holiday_skips_etrade_token_fetch(self):
        """On holidays, is_market_hours should return False without fetching E*TRADE tokens."""
        tz_la = pytz.timezone("America/Los_Angeles")
        # Memorial Day is Monday, May 25, 2026
        # Let's use 7:00 AM PST
        holiday_dt = tz_la.localize(datetime.datetime(2026, 5, 25, 7, 0, 0))

        class MockDatetime(datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                if tz is not None:
                    return holiday_dt.astimezone(tz)
                return holiday_dt

        m_get_tokens = mock.patch("ai_portfolio_game.etrade.get_tokens")
        mock_get_tokens = m_get_tokens.start()
        self.addCleanup(m_get_tokens.stop)

        with mock.patch("ai_portfolio_game.datetime.datetime", MockDatetime):
            is_hours = game.is_market_hours()

        self.assertFalse(is_hours)
        mock_get_tokens.assert_not_called()


if __name__ == "__main__":
    unittest.main()
