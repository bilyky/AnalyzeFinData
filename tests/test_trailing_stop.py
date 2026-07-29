"""
Dedicated unit tests for the AETHER Profit-Lock Trailing Stop-Loss Ratchet inside ai_portfolio_game.py.
"""
import os
import sys
import unittest
import openpyxl
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ai_portfolio_game as game

class TestTrailingStopRatcheting(unittest.TestCase):
    @mock.patch("ai_portfolio_game.is_market_hours", return_value=True)
    @mock.patch("ai_portfolio_game.get_live_prices")
    @mock.patch("ai_portfolio_game.load_game")
    @mock.patch("ai_portfolio_game.save_game")
    @mock.patch("ai_portfolio_game.openpyxl.load_workbook")
    def test_flower_protection_below_cost(self, mock_load_wb, mock_save_game, mock_load_game, mock_get_prices, mock_market_hours):
        # 1. Simulate a position trading BELOW cost (Flower Protection active)
        # Cost = $100.00, stop_loss = $90.00, ATR = $4.00
        # Current price = $98.00 (below cost, no ratchet should trigger)
        state = {
            "balance": 5000.0,
            "equity": 10000.0,
            "positions": {
                "ULTA": {
                    "qty": 10,
                    "cost": 100.0,
                    "stop_loss": 90.0,
                    "is_scarcity": False
                }
            },
            "queued_orders": [],
            "history": []
        }
        mock_load_game.return_value = state
        mock_get_prices.return_value = {"ULTA": 98.0} # Pullback below cost

        # Mock workbook sheet to prevent key errors
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Research"
        ws.append(["Rank", "Symbol", "Industry", "Ticker", "Sector", "Other", "PGR", "Other", "Other", "Other", "Price", "Other", "Other", "Other", "Other", "Other", "Other", "Other", "Other", "Other", "Setup", "Other", "Other", "Win%", "Short10", "Long60"])
        ws.append([1, None, None, "ULTA", "Retail", None, "Bu", None, None, None, 98.0, None, None, None, None, None, None, None, None, None, "0", None, None, 0.65, 5.0, 5.0])
        mock_load_wb.return_value = wb

        # Mock ATR calculation: ATR = 4.00
        with mock.patch("aether.risk_utils.calculate_atr", return_value=4.00):
            game.run_daily_ai_management(force=True, manual_profile="BALANCED")

        # 2. Assert that the stop loss remained strictly unchanged at $90.00
        pos = state["positions"]["ULTA"]
        self.assertEqual(pos["stop_loss"], 90.0)
        self.assertEqual(pos["highest_close_since_acq"], 100.0) # initialized to max(cost, price)

    @mock.patch("ai_portfolio_game.is_market_hours", return_value=True)
    @mock.patch("ai_portfolio_game.get_live_prices")
    @mock.patch("ai_portfolio_game.load_game")
    @mock.patch("ai_portfolio_game.save_game")
    @mock.patch("ai_portfolio_game.openpyxl.load_workbook")
    def test_profit_lock_ratchet_above_cost(self, mock_load_wb, mock_save_game, mock_load_game, mock_get_prices, mock_market_hours):
        # 1. Simulate a position trading ABOVE cost by > 1.0x ATR (Flower Protection satisfied!)
        # Cost = $100.00, stop_loss = $90.00, ATR = $4.00. Multiplier = 2.5 (BALANCED default)
        # Current price = $105.00 (which is > 1.0x ATR above cost!)
        state = {
            "balance": 5000.0,
            "equity": 10000.0,
            "positions": {
                "ULTA": {
                    "qty": 10,
                    "cost": 100.0,
                    "stop_loss": 90.0,
                    "is_scarcity": False
                }
            },
            "queued_orders": [],
            "history": []
        }
        mock_load_game.return_value = state
        mock_get_prices.return_value = {"ULTA": 105.0} # Rallying

        # Mock workbook sheet
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Research"
        ws.append(["Rank", "Symbol", "Industry", "Ticker", "Sector", "Other", "PGR", "Other", "Other", "Other", "Price", "Other", "Other", "Other", "Other", "Other", "Other", "Other", "Other", "Other", "Setup", "Other", "Other", "Win%", "Short10", "Long60"])
        ws.append([1, None, None, "ULTA", "Retail", None, "Bu", None, None, None, 105.0, None, None, None, None, None, None, None, None, None, "0", None, None, 0.65, 5.0, 5.0])
        mock_load_wb.return_value = wb

        # Mock ATR calculation: ATR = 4.00
        with mock.patch("aether.risk_utils.calculate_atr", return_value=4.00):
            game.run_daily_ai_management(force=True, manual_profile="BALANCED")

        # 2. Assert that the stop loss ratcheted upwards successfully
        # Peak close = $105.00
        # Recalculated stop = 105.00 - (2.5 * 4.00) = 105.00 - 10.00 = $95.00!
        # Since $95.00 > $90.00, stop should be updated to $95.00!
        pos = state["positions"]["ULTA"]
        self.assertEqual(pos["stop_loss"], 95.0)
        self.assertEqual(pos["highest_close_since_acq"], 105.0)

    @mock.patch("ai_portfolio_game.is_market_hours", return_value=True)
    @mock.patch("ai_portfolio_game.get_live_prices")
    @mock.patch("ai_portfolio_game.load_game")
    @mock.patch("ai_portfolio_game.save_game")
    @mock.patch("ai_portfolio_game.openpyxl.load_workbook")
    def test_breakeven_lock_trigger(self, mock_load_wb, mock_save_game, mock_load_game, mock_get_prices, mock_market_hours):
        # 1. Simulate a position trading ABOVE cost by > 1.5x ATR (Breakeven active!)
        # Cost = $100.00, stop_loss = $90.00, ATR = $4.00. Multiplier = 2.5
        # Current price = $107.00 (which is > 1.5x ATR above cost!)
        state = {
            "balance": 5000.0,
            "equity": 10000.0,
            "positions": {
                "ULTA": {
                    "qty": 10,
                    "cost": 100.0,
                    "stop_loss": 90.0,
                    "is_scarcity": False
                }
            },
            "queued_orders": [],
            "history": []
        }
        mock_load_game.return_value = state
        mock_get_prices.return_value = {"ULTA": 107.0} # High breakout rally

        # Mock workbook sheet
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Research"
        ws.append(["Rank", "Symbol", "Industry", "Ticker", "Sector", "Other", "PGR", "Other", "Other", "Other", "Price", "Other", "Other", "Other", "Other", "Other", "Other", "Other", "Other", "Other", "Setup", "Other", "Other", "Win%", "Short10", "Long60"])
        ws.append([1, None, None, "ULTA", "Retail", None, "Bu", None, None, None, 107.0, None, None, None, None, None, None, None, None, None, "0", None, None, 0.65, 5.0, 5.0])
        mock_load_wb.return_value = wb

        # Mock ATR calculation: ATR = 4.00
        with mock.patch("aether.risk_utils.calculate_atr", return_value=4.00):
            game.run_daily_ai_management(force=True, manual_profile="BALANCED")

        # 2. Assert that the stop loss ratcheted upwards and triggered Breakeven Lock at cost basis ($100.00)
        # Recalculated stop = 107.00 - (2.5 * 4.00) = 107.00 - 10.00 = $97.00.
        # But Breakeven Lock triggers because (107.00 - 100.00) > (1.5 * 4.00) -> 7.00 > 6.00!
        # So the stop loss is bumped all the way to cost basis ($100.00), guaranteeing a risk-free trade!
        pos = state["positions"]["ULTA"]
        self.assertEqual(pos["stop_loss"], 100.0)
        self.assertEqual(pos["highest_close_since_acq"], 107.0)

if __name__ == "__main__":
    unittest.main()
