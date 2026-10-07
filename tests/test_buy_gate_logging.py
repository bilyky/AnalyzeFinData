"""A buy candidate that clears every gate but the profile's score threshold must say so in
the log — PROD 2026-09-30 dropped GNE (9.5 < DEFENSIVE 10.0) with no trace."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ai_portfolio_game as game
from tests._helpers import research_workbook


def _row(sym, price, stop, target, s10, l60):
    row = [1, None, None, sym, "Retail", None, "Bu", None, price, stop, price, target, None, None,
           None, None, None, None, None, None, "1", None, None, 0.6, s10, l60]
    return row


@mock.patch("ai_portfolio_game.is_market_hours", return_value=True)
@mock.patch("ai_portfolio_game.get_live_prices")
@mock.patch("ai_portfolio_game.load_game")
@mock.patch("ai_portfolio_game.save_game")
@mock.patch("ai_portfolio_game.openpyxl.load_workbook")
class TestProfileThresholdRejectionIsLogged(unittest.TestCase):
    def setUp(self):
        self.patchers = [mock.patch("ai_client.evaluate", return_value=""),
                         mock.patch("ai_portfolio_game._heal_symbol_cache", return_value=False),
                         mock.patch("ai_portfolio_game._cache_stale", return_value=False),
                         mock.patch("ai_portfolio_game.is_bottom_confirmed", return_value=(False, "no")),
                         mock.patch("ai_portfolio_game.backtrack_verify", return_value=(True, "ok")),
                         mock.patch("ai_portfolio_game.calculate_bubble_z_score", return_value=None)]
        for p in self.patchers:
            p.start()

    def tearDown(self):
        for p in self.patchers:
            p.stop()

    def _run(self, mock_load_wb, mock_load_game, mock_get_prices, s10, l60):
        state = {"balance": 10000.0, "equity": 10000.0, "queued_orders": [], "history": [], "positions": {}}
        mock_load_game.return_value = state
        mock_get_prices.return_value = {"GNE": 100.0, "SPY": 500.0}
        mock_load_wb.return_value = research_workbook(_row("GNE", 100.0, 95.0, 120.0, s10, l60))
        with mock.patch.object(game, "_log", wraps=game._log) as log:
            game.run_daily_ai_management(force=True, manual_profile="DEFENSIVE")
        self.history = state["history"]
        return [str(c.args[0]) for c in log.warning.call_args_list if c.args]

    def test_below_threshold_is_logged(self, mock_load_wb, _save, mock_load_game, mock_get_prices, _mh):
        warnings = self._run(mock_load_wb, mock_load_game, mock_get_prices, s10=5.7, l60=3.8)  # 9.5 < 10
        hits = [w for w in warnings if "Profile Threshold" in w and "GNE" in w]
        self.assertEqual(len(hits), 1, warnings)
        self.assertIn("9.5", hits[0])
        self.assertIn("DEFENSIVE", hits[0])
        self.assertFalse([t for t in self.history if t["type"] == "BUY"])

    def test_above_threshold_is_not_logged_as_rejected(self, mock_load_wb, _save, mock_load_game, mock_get_prices, _mh):
        warnings = self._run(mock_load_wb, mock_load_game, mock_get_prices, s10=6.0, l60=5.0)  # 11 >= 10
        self.assertFalse([w for w in warnings if "Profile Threshold" in w], warnings)
        # It cleared the threshold and was actually bought.
        self.assertEqual([t["symbol"] for t in self.history if t["type"] == "BUY"], ["GNE"])


if __name__ == "__main__":
    unittest.main()
