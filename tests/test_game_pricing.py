"""
Tests for the capital-safety pricing fixes in ai_portfolio_game:
 - _live_equity falls back to cost so a missing quote can't crash equity/sizing.
 - get_live_prices fills only the missing symbols from Google (partial-fill),
   preserving good E*TRADE quotes instead of discarding and re-scraping all.
E*TRADE + Google are mocked; no network.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ai_portfolio_game as game


class TestLiveEquity(unittest.TestCase):
    POS = {"AAA": {"qty": 10, "cost": 5.0}, "BBB": {"qty": 2, "cost": 100.0}}

    def test_all_priced(self):
        eq = game._live_equity(1000.0, self.POS, {"AAA": 6.0, "BBB": 110.0})
        self.assertEqual(eq, 1000.0 + 10 * 6.0 + 2 * 110.0)

    def test_missing_quote_falls_back_to_cost(self):
        eq = game._live_equity(0.0, self.POS, {"AAA": 6.0})  # BBB missing
        self.assertEqual(eq, 10 * 6.0 + 2 * 100.0)           # BBB at cost

    def test_zero_or_negative_price_falls_back_to_cost(self):
        eq = game._live_equity(0.0, self.POS, {"AAA": 0, "BBB": -5})
        self.assertEqual(eq, 10 * 5.0 + 2 * 100.0)

    def test_empty_positions_returns_cash(self):
        self.assertEqual(game._live_equity(750.0, {}, {}), 750.0)


class TestGetLivePricesPartialFill(unittest.TestCase):
    def test_only_missing_symbols_scraped(self):
        with mock.patch.object(game, "is_market_hours", return_value=True), \
             mock.patch.object(game.etrade, "get_tokens", return_value=["tok"]), \
             mock.patch.object(game.etrade, "fetch_quotes",
                               return_value={"AAA": 10.0}), \
             mock.patch.object(game, "get_google_prices_fallback",
                               return_value={"BBB": 20.0}) as goog:
            out = game.get_live_prices(["AAA", "BBB"])
        # E*TRADE quote preserved, gap filled, and only the gap was scraped.
        self.assertEqual(out, {"AAA": 10.0, "BBB": 20.0})
        goog.assert_called_once_with(["BBB"])

    def test_no_missing_means_no_scrape(self):
        with mock.patch.object(game, "is_market_hours", return_value=True), \
             mock.patch.object(game.etrade, "get_tokens", return_value=["tok"]), \
             mock.patch.object(game.etrade, "fetch_quotes",
                               return_value={"AAA": 10.0, "BBB": 20.0}), \
             mock.patch.object(game, "get_google_prices_fallback") as goog:
            out = game.get_live_prices(["AAA", "BBB"])
        self.assertEqual(out, {"AAA": 10.0, "BBB": 20.0})
        goog.assert_not_called()


if __name__ == "__main__":
    unittest.main()
