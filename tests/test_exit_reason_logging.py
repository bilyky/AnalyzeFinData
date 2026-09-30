"""The live exit path records why a position closed (exit reason, stop fill, stop level)
on its SELL transaction, so the ledger can separate stop-outs from momentum exits."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ai_portfolio_game as game
from tests._helpers import research_workbook


def _row(sym, price, s10, l60):
    return [1, None, None, sym, "Retail", None, "Wk", None, None, None, price, None, None, None,
            None, None, None, None, None, None, "0", None, None, 0.30, s10, l60]


@mock.patch("ai_portfolio_game.is_market_hours", return_value=True)
@mock.patch("ai_portfolio_game.get_live_prices")
@mock.patch("ai_portfolio_game.load_game")
@mock.patch("ai_portfolio_game.save_game")
@mock.patch("ai_portfolio_game.openpyxl.load_workbook")
class TestExitReasonLogging(unittest.TestCase):
    def setUp(self):
        game._HEAL_ATTEMPTED.clear()
        self.patchers = [mock.patch("ai_client.evaluate", return_value=""),
                         mock.patch("ai_portfolio_game._heal_symbol_cache", return_value=False)]
        for p in self.patchers:
            p.start()

    def tearDown(self):
        for p in self.patchers:
            p.stop()

    def _run(self, mock_load_wb, mock_load_game, mock_get_prices, sym, cost, stop, price, s10, l60):
        state = {"balance": 5000.0, "equity": 10000.0, "queued_orders": [], "history": [],
                 "positions": {sym: {"qty": 10, "cost": cost}}}
        if stop is not None:
            state["positions"][sym]["stop_loss"] = stop
        mock_load_game.return_value = state
        mock_get_prices.return_value = {sym: price}
        mock_load_wb.return_value = research_workbook(_row(sym, price, s10, l60))
        game.run_daily_ai_management(force=True, manual_profile="BALANCED")
        sells = [t for t in state["history"] if t.get("type") == "SELL" and t.get("symbol") == sym]
        self.assertEqual(len(sells), 1, "expected exactly one live SELL")
        return sells[0]

    def test_momentum_exit_records_reason_and_stop(self, mock_load_wb, _save, mock_load_game, mock_get_prices, _mh):
        tx = self._run(mock_load_wb, mock_load_game, mock_get_prices, "TSCO", 100.0, 80.0, 90.0, -8.0, -8.0)
        self.assertTrue(tx["details"].startswith("Exit: momentum decay"), tx["details"])
        self.assertNotIn("STP LMT", tx["details"])
        self.assertEqual(tx["stop_loss"], 80.0)

    def test_stop_breach_records_reason_and_stop_fill(self, mock_load_wb, _save, mock_load_game, mock_get_prices, _mh):
        tx = self._run(mock_load_wb, mock_load_game, mock_get_prices, "TSCO", 100.0, 80.0, 78.0, 1.0, 1.0)
        self.assertIn("stop breached", tx["details"])
        self.assertIn("[STP LMT fill]", tx["details"])
        self.assertEqual(tx["price"], 80.0)
        self.assertEqual(tx["stop_loss"], 80.0)

    def test_missing_stop_uses_fallback_and_records_none(self, mock_load_wb, _save, mock_load_game, mock_get_prices, _mh):
        # No stored stop -> exit_decision's cost*(1-8%) fallback (92.0) fires; the tx must not
        # claim a 0.0 stop or a STP LMT fill it never had.
        tx = self._run(mock_load_wb, mock_load_game, mock_get_prices, "TSCO", 100.0, None, 90.0, 1.0, 1.0)
        self.assertIn("stop breached", tx["details"])
        self.assertNotIn("STP LMT", tx["details"])
        self.assertIsNone(tx["stop_loss"])
        self.assertEqual(tx["price"], 90.0)

    def test_queued_sell_records_stop(self, mock_load_wb, _save, mock_load_game, mock_get_prices, _mh):
        state = {"balance": 5000.0, "equity": 10000.0, "history": [],
                 "queued_orders": [{"type": "SELL", "symbol": "TSCO", "reason": "Exit triggered: test"}],
                 "positions": {"TSCO": {"qty": 10, "cost": 100.0, "stop_loss": 80.0}}}
        mock_load_game.return_value = state
        mock_get_prices.return_value = {"TSCO": 95.0}
        mock_load_wb.return_value = research_workbook(_row("TSCO", 95.0, 5.0, 5.0))
        game.run_daily_ai_management(force=True, manual_profile="BALANCED")
        sells = [t for t in state["history"] if t.get("type") == "SELL"]
        self.assertEqual(len(sells), 1)
        self.assertEqual(sells[0]["details"], "Queued Sell: Exit triggered: test")
        self.assertEqual(sells[0]["stop_loss"], 80.0)


if __name__ == "__main__":
    unittest.main()
