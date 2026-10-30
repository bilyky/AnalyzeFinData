"""Pyramiding adds (R&D #31) follow the same risk rules as new buys.

A scale-in used to size by allocation room and cash only, so the added shares could lose more
than CFG.system_max_trade_risk_pct of equity at the stop, and it added to positions whose stop
was still below cost although R&D #31 is about protected positions. The pyramiding gate is
forced to "yes" so the pass's own checks decide.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ai_portfolio_game as game
from aether.config import CFG
from tests._helpers import research_workbook


def _row(sym, price, s10, l60):
    return [1, None, None, sym, "Retail", None, "Bu", None, None, None, price, None, None, None,
            None, None, None, None, None, None, "0", None, None, 0.30, s10, l60]


def _run(sym, price, pos):
    state = {"balance": 9000.0, "equity": 10000.0, "queued_orders": [], "history": [],
             "positions": {sym: pos}}
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
         mock.patch.object(game.risk_utils, "calculate_atr", return_value=2.0), \
         mock.patch.object(game.risk_utils, "scale_out_plan", return_value=(0.0, "")), \
         mock.patch.object(game, "should_pyramid_into_winner", return_value=True), \
         mock.patch("ai_client.evaluate", return_value=""):
        game.run_daily_ai_management(force=True, manual_profile="BALANCED")
    return state, [t for t in state["history"] if t.get("symbol") == sym and t["type"] == "BUY_SCALE_IN"]


class TestPyramidRiskRules(unittest.TestCase):
    def test_add_is_capped_by_the_per_trade_loss_limit(self):
        sym, price, stop = "GNE", 16.685, 15.0
        state, adds = _run(sym, price, {"qty": 20, "cost": 15.0, "stop_loss": stop,
                                        "highest_close_since_acq": price})
        self.assertEqual(len(adds), 1, f"no scale-in fired, so this test proves nothing: {state['history']}")
        self.assertEqual(state["positions"][sym]["stop_loss"], stop, "the run moved the stop; fixture no longer tests the cap")
        add_risk = adds[0]["qty"] * (price - stop)
        self.assertLessEqual(add_risk, 10000.0 * CFG.system_max_trade_risk_pct + 1e-6)

    def test_no_add_while_the_stop_is_below_cost(self):
        sym, price = "GNE", 16.685
        state, adds = _run(sym, price, {"qty": 20, "cost": 15.0, "stop_loss": 14.0,
                                        "highest_close_since_acq": price})
        self.assertEqual(state["positions"][sym]["stop_loss"], 14.0, "the run moved the stop; fixture no longer tests the rule")
        self.assertEqual(adds, [])
        self.assertEqual(state["positions"][sym]["qty"], 20)


if __name__ == "__main__":
    unittest.main()
