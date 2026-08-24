"""The universal human-initiated re-auth API (aether.etrade.reauthenticate).

reauthenticate() is the single core every front door calls (CLI etrade-login, POST
/api/etrade/reauth, web button). It must:
  * drive get_tokens(allow_browser=True) and return a JSON-serializable result dict;
  * FAIL SOFT — never propagate an exception — so an HTTP handler / CLI renders a clean
    error instead of a 500 / traceback;
  * treat a token that saves but cannot fetch a live quote as a FAILURE (ok=False), not a
    success (a token is only good if it actually authorizes);
  * force a HEADED browser under bootstrap=True (a human must enter the one-time OTP),
    while honoring headless= on the ordinary path.

Every case mocks get_tokens / get_market — no E*TRADE contact.
"""
import json
import unittest
from unittest import mock

from aether import etrade


_TOKENS = {"oauth_token": "t", "oauth_token_secret": "s", "issued_date_et": "2026-08-19"}


def _market_quoting(price):
    """A fake ETradeMarket whose get_quote yields a single AAPL lastTrade of `price`."""
    m = mock.MagicMock()
    m.get_quote.return_value = {"QuoteResponse": {"QuoteData": [{"All": {"lastTrade": price}}]}}
    return m


class TestReauthenticate(unittest.TestCase):
    def setUp(self):
        # Keep the breaker snapshot deterministic and off disk.
        p = mock.patch.object(etrade, "_breaker_summary",
                              return_value={"consecutive_failures": 0, "cooldown_remaining_min": 0.0})
        p.start()
        self.addCleanup(p.stop)

    def test_success_returns_ok_dict(self):
        with mock.patch.object(etrade, "get_tokens", return_value=dict(_TOKENS)) as gt, \
             mock.patch.object(etrade, "get_market", return_value=_market_quoting(231.5)):
            res = etrade.reauthenticate("production")
        gt.assert_called_once_with("production", allow_browser=True, headless=False)
        self.assertTrue(res["ok"])
        self.assertTrue(res["has_token"])
        self.assertTrue(res["quote_ok"])
        self.assertEqual(res["issued_date_et"], "2026-08-19")
        self.assertEqual(res["aapl"], 231.5)
        # The whole point of the dict: it crosses the HTTP boundary cleanly.
        json.dumps(res)

    def test_no_token_is_soft_fail(self):
        with mock.patch.object(etrade, "get_tokens", return_value=None):
            res = etrade.reauthenticate("production")
        self.assertFalse(res["ok"])
        self.assertFalse(res["has_token"])
        self.assertIn("no token", res["message"].lower())
        json.dumps(res)

    def test_token_but_dead_quote_is_failure(self):
        # A token that mints but cannot fetch a live quote is NOT a success.
        with mock.patch.object(etrade, "get_tokens", return_value=dict(_TOKENS)), \
             mock.patch.object(etrade, "get_market", side_effect=RuntimeError("401")):
            res = etrade.reauthenticate("production")
        self.assertTrue(res["has_token"])
        self.assertFalse(res["quote_ok"])
        self.assertFalse(res["ok"])
        self.assertIn("smoke quote failed", res["message"])

    def test_zero_price_quote_is_failure(self):
        # A 0.0 lastTrade (stale/blank payload) must not read as authorized.
        with mock.patch.object(etrade, "get_tokens", return_value=dict(_TOKENS)), \
             mock.patch.object(etrade, "get_market", return_value=_market_quoting(0.0)):
            res = etrade.reauthenticate("production")
        self.assertFalse(res["quote_ok"])
        self.assertFalse(res["ok"])

    def test_get_tokens_exception_is_caught(self):
        # A crash inside the browser path must come back as a soft error, never propagate.
        with mock.patch.object(etrade, "get_tokens", side_effect=RuntimeError("boom")):
            res = etrade.reauthenticate("production")
        self.assertFalse(res["ok"])
        self.assertIn("raised", res["message"])
        json.dumps(res)

    def test_bootstrap_forces_headed_even_if_headless_requested(self):
        # bootstrap needs a human at the OTP prompt → headed, overriding headless=True.
        with mock.patch.object(etrade, "get_tokens", return_value=dict(_TOKENS)) as gt, \
             mock.patch.object(etrade, "get_market", return_value=_market_quoting(1.0)):
            etrade.reauthenticate("production", bootstrap=True, headless=True)
        gt.assert_called_once_with("production", allow_browser=True, headless=False)

    def test_headless_passthrough_on_ordinary_path(self):
        with mock.patch.object(etrade, "get_tokens", return_value=dict(_TOKENS)) as gt, \
             mock.patch.object(etrade, "get_market", return_value=_market_quoting(1.0)):
            etrade.reauthenticate("production", headless=True)
        gt.assert_called_once_with("production", allow_browser=True, headless=True)


if __name__ == "__main__":
    unittest.main()
