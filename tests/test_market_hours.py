"""
Unit tests for the NYSE market-hours check and its FORCE_MARKET_CLOSED seam.
"""
import datetime
import os
import sys
import unittest
from unittest import mock

import pytz

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import powergauge

_NY = pytz.timezone("America/New_York")


class TestMarketHours(unittest.TestCase):

    def setUp(self):
        # Each test starts with the override absent; any pre-existing value is
        # captured here and restored in tearDown so we never leak env state.
        self._saved = os.environ.pop("FORCE_MARKET_CLOSED", None)

    def tearDown(self):
        if self._saved is not None:
            os.environ["FORCE_MARKET_CLOSED"] = self._saved
        else:
            os.environ.pop("FORCE_MARKET_CLOSED", None)

    def _at(self, dt):
        """Patch powergauge's clock to a fixed New York datetime."""
        patcher = mock.patch("powergauge.datetime")
        m_dt = patcher.start()
        self.addCleanup(patcher.stop)
        m_dt.datetime.now.return_value = _NY.localize(dt)
        return m_dt

    def test_open_during_weekday_session(self):
        """Without the override, a Wednesday 11:00 ET reads as OPEN."""
        self._at(datetime.datetime(2026, 8, 5, 11, 0, 0))
        self.assertTrue(powergauge.is_nyse_market_open())

    def test_force_market_closed_overrides_open_session(self):
        """Same Wednesday 11:00 ET, but FORCE_MARKET_CLOSED=true forces CLOSED.

        Paired with test_open_during_weekday_session (identical timestamp, opposite
        result) so the override branch is isolated from the clock logic."""
        self._at(datetime.datetime(2026, 8, 5, 11, 0, 0))
        with mock.patch.dict(os.environ, {"FORCE_MARKET_CLOSED": "true"}):
            self.assertFalse(powergauge.is_nyse_market_open())

    def test_closed_on_weekend(self):
        """A Saturday reads as CLOSED regardless of clock time."""
        self._at(datetime.datetime(2026, 8, 8, 11, 0, 0))
        self.assertFalse(powergauge.is_nyse_market_open())

    def test_closed_before_open_bell(self):
        """08:00 ET on a weekday is before the 09:30 bell — CLOSED."""
        self._at(datetime.datetime(2026, 8, 5, 8, 0, 0))
        self.assertFalse(powergauge.is_nyse_market_open())


if __name__ == "__main__":
    unittest.main()
