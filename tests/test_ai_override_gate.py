"""
Dedicated unit tests for the AI Second-Opinion Exit Override Gate in ai_portfolio_game.py.
"""
import os
import sys
import unittest
import openpyxl
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ai_portfolio_game as game

class TestAIOverrideGate(unittest.TestCase):
    @mock.patch("ai_portfolio_game.is_market_hours", return_value=True)
    @mock.patch("ai_portfolio_game.get_live_prices")
    @mock.patch("ai_portfolio_game.load_game")
    @mock.patch("ai_portfolio_game.save_game")
    @mock.patch("ai_portfolio_game.openpyxl.load_workbook")
    def test_sell_overridden_and_downgraded_to_watch(self, mock_load_wb, mock_save_game, mock_load_game, mock_get_prices, mock_market_hours):
        # 1. Simulate an active position with a shadow verdict of "FLAG-FOR-REVIEW"
        # The position "ULTA" is set to have negative trend metrics, which would normally trigger a deterministic "SELL"
        state = {
            "balance": 5000.0,
            "equity": 10000.0,
            "positions": {
                "ULTA": {
                    "qty": 3,
                    "cost": 469.56,
                    "stop_loss": 400.0,
                    "shadow_verdict": "FLAG-FOR-REVIEW"  # Stored shadow verdict to trip the override gate!
                }
            },
            "queued_orders": [],
            "history": []
        }
        mock_load_game.return_value = state
        
        # Current price of ULTA is above stop loss but the trend decay (S10/L60 both negative) triggers a soft exit
        mock_get_prices.return_value = {"ULTA": 450.0}

        # Mock the workbook sheets to define negative trend scores for ULTA
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Research"
        ws.append(["Rank", "Symbol", "Industry", "Ticker", "Sector", "Other", "PGR", "Other", "Other", "Other", "Price", "Other", "Other", "Other", "Other", "Other", "Other", "Other", "Other", "Other", "Setup", "Other", "Other", "Win%", "Short10", "Long60"])
        # Short10 = -5.0, Long60 = -5.0 triggers trend decay/momentum SELL signal
        ws.append([1, None, None, "ULTA", "Retail", None, "Bu", None, None, None, 450.0, None, None, None, None, None, None, None, None, None, "0", None, None, 0.65, -5.0, -5.0])
        mock_load_wb.return_value = wb

        # 2. Run the game daily management (force=True to bypass market holiday/clock checks)
        game.run_daily_ai_management(force=True, manual_profile="BALANCED")

        # 3. Assert that the SELL was successfully overridden and downgraded to "WATCH"
        # Since it's WATCH (not SELL), it must NOT be sold (i.e. still in the active positions)
        self.assertIn("ULTA", state["positions"])
        self.assertEqual(len(state["queued_orders"]), 0)

    @mock.patch("ai_portfolio_game.is_market_hours", return_value=True)
    @mock.patch("ai_portfolio_game.get_live_prices")
    @mock.patch("ai_portfolio_game.load_game")
    @mock.patch("ai_portfolio_game.save_game")
    @mock.patch("ai_portfolio_game.openpyxl.load_workbook")
    def test_sell_overridden_and_downgraded_to_hold(self, mock_load_wb, mock_save_game, mock_load_game, mock_get_prices, mock_market_hours):
        # 1. Simulate an active position with a shadow verdict of "HOLD" inside the active state
        state = {
            "balance": 5000.0,
            "equity": 10000.0,
            "positions": {
                "GMED": {
                    "qty": 20,
                    "cost": 74.80,
                    "stop_loss": 70.0,
                    "shadow_verdict": {"verdict": "HOLD", "note": "Strong structural reasons to hold"}
                }
            },
            "queued_orders": [],
            "history": []
        }
        mock_load_game.return_value = state
        mock_get_prices.return_value = {"GMED": 72.0}

        # Mock the workbook sheets to define negative trend scores for GMED
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Research"
        ws.append(["Rank", "Symbol", "Industry", "Ticker", "Sector", "Other", "PGR", "Other", "Other", "Other", "Price", "Other", "Other", "Other", "Other", "Other", "Other", "Other", "Other", "Other", "Setup", "Other", "Other", "Win%", "Short10", "Long60"])
        # Short10 = -3.0, Long60 = -3.0 triggers soft momentum exit/SELL signal
        ws.append([1, None, None, "GMED", "Medical", None, "Bu", None, None, None, 72.0, None, None, None, None, None, None, None, None, None, "0", None, None, 0.65, -3.0, -3.0])
        mock_load_wb.return_value = wb

        # 2. Run the game daily management (force=True)
        game.run_daily_ai_management(force=True, manual_profile="BALANCED")

        # 3. Assert that the SELL was overridden and downgraded to "HOLD" (retaining position)
        self.assertIn("GMED", state["positions"])
        self.assertEqual(len(state["queued_orders"]), 0)

if __name__ == "__main__":
    unittest.main()
