"""A symbol is never scaled out and scaled back in during the same run.

PROD 2026-10-02 07:00:03: GNE sold 58 sh (Bank-As-You-Go scale-out) and bought 55 sh
(Pyramiding Scale-In), both at 16.685. That books "realized" profit while putting the
exposure straight back (also COIN 09-21, SPCX 10-06). The scale-out planner and the
pyramiding gate are forced to "yes" so both fire on one position; the real run decides.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ai_portfolio_game as game
from tests._helpers import research_workbook


def _row(sym, price, s10, l60):
    return [1, None, None, sym, "Retail", None, "Bu", None, None, None, price, None, None, None,
            None, None, None, None, None, None, "0", None, None, 0.30, s10, l60]


class TestNoScaleInAfterScaleOut(unittest.TestCase):
    def test_scaled_out_symbol_is_not_pyramided_in_the_same_run(self):
        sym, price = "GNE", 16.685
        state = {"balance": 9000.0, "equity": 10000.0, "queued_orders": [], "history": [],
                 "positions": {sym: {"qty": 100, "cost": 15.0, "stop_loss": 14.0,
                                     "highest_close_since_acq": price}}}
        game._HEAL_ATTEMPTED.clear()
        with mock.patch.object(game, "is_market_hours", return_value=True), \
             mock.patch.object(game, "get_live_prices", return_value={sym: price, "SPY": 500.0}), \
             mock.patch.object(game, "load_game", return_value=state), \
             mock.patch.object(game, "save_game"), \
             mock.patch.object(game, "update_excel_log"), \
             mock.patch.object(game.openpyxl, "load_workbook",
                               return_value=research_workbook(_row(sym, price, 7.7, 5.0))), \
             mock.patch.object(game, "_heal_symbol_cache", return_value=False), \
             mock.patch.object(game, "workbook_staleness", return_value=None), \
             mock.patch.object(game.risk_utils, "calculate_atr", return_value=0.5), \
             mock.patch.object(game.risk_utils, "scale_out_plan", return_value=(0.6, "bank 60% (test)")), \
             mock.patch.object(game, "should_pyramid_into_winner", return_value=True), \
             mock.patch("ai_client.evaluate", return_value=""):
            game.run_daily_ai_management(force=True, manual_profile="BALANCED")
        mine = [t for t in state["history"] if t.get("symbol") == sym]
        types = [t["type"] for t in mine]
        self.assertIn("SELL", types, f"scale-out did not fire, so this test proves nothing: {mine}")
        self.assertNotIn("BUY_SCALE_IN", types, f"scaled out and back in in one run: {mine}")


if __name__ == "__main__":
    unittest.main()
