"""
Unit tests for the profit-lock trailing stop ratchet in ai_portfolio_game.py.
"""
import os
import sys
import unittest
import openpyxl
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ai_portfolio_game as game


def _make_state(price, cost=100.0, stop=90.0):
    return {
        "balance": 5000.0,
        "equity": 10000.0,
        "positions": {
            "ULTA": {"qty": 10, "cost": cost, "stop_loss": stop, "is_scarcity": False}
        },
        "queued_orders": [],
        "history": [],
    }


def _make_wb(sym, price, s10=5.0, l60=5.0):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Research"
    ws.append(["Rank", "Symbol", "Industry", "Ticker", "Sector", "Other", "PGR",
               "Other", "Other", "Other", "Price", "Other", "Other", "Other", "Other",
               "Other", "Other", "Other", "Other", "Other", "Setup", "Other", "Other",
               "Win%", "Short10", "Long60"])
    ws.append([1, None, None, sym, "Retail", None, "Bu", None, None, None, price,
               None, None, None, None, None, None, None, None, None, "0", None, None,
               0.65, s10, l60])
    return wb


_COMMON_PATCHES = [
    mock.patch("ai_portfolio_game.is_market_hours", return_value=True),
    mock.patch("ai_portfolio_game.get_live_prices"),
    mock.patch("ai_portfolio_game.load_game"),
    mock.patch("ai_portfolio_game.save_game"),
    mock.patch("ai_portfolio_game.openpyxl.load_workbook"),
    mock.patch("ai_portfolio_game.circuit_breaker.enforce_circuit_breaker"),
]


class TestTrailingStopRatcheting(unittest.TestCase):

    def setUp(self):
        game._HEAL_ATTEMPTED.clear()

    def _run(self, state, price, atr=4.0):
        patches = [p.start() for p in _COMMON_PATCHES]
        try:
            _mkt, _prices, _load, _save, _wb, _cb = patches
            _prices.return_value = {"ULTA": price, "SPY": 500.0}
            _load.return_value = state
            _wb.return_value = _make_wb("ULTA", price)
            with mock.patch("aether.risk_utils.calculate_atr", return_value=atr):
                game.run_daily_ai_management(force=True, manual_profile="BALANCED")
        finally:
            for p in _COMMON_PATCHES:
                p.stop()

    def test_flower_protection_below_cost(self):
        """Stop does NOT ratchet when price is below cost."""
        state = _make_state(price=98.0)
        self._run(state, price=98.0)
        pos = state["positions"]["ULTA"]
        self.assertEqual(pos["stop_loss"], 90.0)
        self.assertEqual(pos["highest_close_since_acq"], 100.0)

    def test_stop_does_not_decrease_on_retrace(self):
        """Red-path: stop must NOT move down when price retraces from a previous peak."""
        state = _make_state(price=95.0, stop=92.0)
        state["positions"]["ULTA"]["highest_close_since_acq"] = 102.0
        self._run(state, price=95.0)
        pos = state["positions"]["ULTA"]
        self.assertGreaterEqual(pos["stop_loss"], 92.0, "Ratcheted stop must never decrease on retrace")

    def test_profit_lock_ratchet_above_cost(self):
        """Stop ratchets up when price is > 1×ATR above cost: 105 - 2.5×4 = $95."""
        state = _make_state(price=105.0)
        self._run(state, price=105.0)
        pos = state["positions"]["ULTA"]
        self.assertEqual(pos["stop_loss"], 95.0)
        self.assertEqual(pos["highest_close_since_acq"], 105.0)

    def test_breakeven_lock_trigger(self):
        """Breakeven lock fires when profit > 1.5×ATR: stop raised to cost basis $100."""
        state = _make_state(price=107.0)
        self._run(state, price=107.0)
        pos = state["positions"]["ULTA"]
        self.assertEqual(pos["stop_loss"], 100.0)
        self.assertEqual(pos["highest_close_since_acq"], 107.0)


if __name__ == "__main__":
    unittest.main()
