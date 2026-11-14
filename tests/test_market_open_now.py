"""etrade.is_market_open_now: True only when a check confirms the market is open, False when one
says closed, None when neither can be read. It used to fall through to True when both the clock
and the SPY quote failed (an expired token's 401s, 2026-11-11). Network calls are faked."""
import datetime
import unittest
from unittest import mock

import pytz

from aether import etrade

NY = pytz.timezone("America/New_York")


class _R:
    def __init__(self, status, body=None):
        self.status_code, self._body = status, body or {}
        self.ok = 200 <= status < 300

    def json(self):
        return self._body


def clock(status):
    return _R(200, {"ClockResponse": {"currentStatus": status}})


def quote(days_ago):
    ts = (datetime.datetime.now(NY) - datetime.timedelta(days=days_ago)).timestamp()
    return _R(200, {"QuoteResponse": {"QuoteData": [{"dateTimeUTC": ts}]}})


class TestMarketOpenNow(unittest.TestCase):
    def _run(self, clock_r, quote_r):
        sess = mock.MagicMock()
        sess.get.side_effect = [clock_r, quote_r]
        with mock.patch.object(etrade, "OAuth1Session", return_value=sess), \
                mock.patch.object(etrade, "_load_config", return_value=("ck", "cs", "u", "p")):
            return etrade.is_market_open_now({"oauth_token": "t", "oauth_token_secret": "s"})

    def test_both_checks_failing_is_unknown_not_open(self):
        self.assertIsNone(self._run(_R(401), _R(401)))
        self.assertIsNone(self._run(_R(503), _R(500)))

    def test_either_check_can_confirm_open(self):
        self.assertIs(self._run(clock("REGULAR"), _R(401)), True)
        self.assertIs(self._run(_R(401), quote(0)), True)
        self.assertIs(self._run(clock("REGULAR"), quote(0)), True)

    def test_either_check_can_say_closed(self):
        self.assertIs(self._run(clock("CLOSED"), quote(0)), False)
        self.assertIs(self._run(clock("REGULAR"), quote(3)), False)      # SPY didn't trade today
        self.assertIs(self._run(_R(401), quote(1)), False)

    def test_a_clock_without_a_status_confirms_nothing(self):
        self.assertIsNone(self._run(_R(200, {}), _R(401)))
        self.assertIsNone(self._run(_R(200, {}), _R(200, {"QuoteResponse": {"QuoteData": []}})))

    def test_a_network_error_is_unknown(self):
        sess = mock.MagicMock()
        sess.get.side_effect = OSError("down")
        with mock.patch.object(etrade, "OAuth1Session", return_value=sess), \
                mock.patch.object(etrade, "_load_config", return_value=("ck", "cs", "u", "p")):
            self.assertIsNone(etrade.is_market_open_now({"oauth_token": "t", "oauth_token_secret": "s"}))


if __name__ == "__main__":
    unittest.main()
