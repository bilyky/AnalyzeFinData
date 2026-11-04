"""Slot expansion counts the cash buffer, and dust buys are skipped (PROD review 2026-11-04).

On PROD 2026-11-03 the game held 10 positions with 20.1% cash under BALANCED (20% buffer).
Slot expansion saw "cash > 15%" and opened an 11th slot, but only ~$16 sat above the buffer,
so the slot bought 1 share of COMP at $9.47. Expansion now needs 15% of equity *above the
buffer*, and any buy or scale-in under CFG.system_min_ticket_usd is skipped.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
import ai_portfolio_game as game
import portfolio_backtest as pb
import tests.test_pyramid_risk_cap as pyr
from aether.config import CFG


class TestSlotExpansionCountsTheBuffer(unittest.TestCase):
    CASES = [
        # (cash_ratio, positions, base, buffer, expected)
        (0.201, 10, 5, 0.20, 5),   # PROD 11-03: was 11
        (0.279, 10, 3, 0.50, 3),   # PROD 11-04 DEFENSIVE: was 11
        (0.40, 5, 5, 0.20, 6),     # 20% above the buffer: one more slot
        (0.20, 5, 5, 0.0, 6),      # no buffer (bull regime): unchanged rule
        (0.20, 4, 5, 0.0, 5),      # slots not full: no expansion
    ]

    def test_cases(self):
        for cash, n, base, buf, want in self.CASES:
            with self.subTest(cash=cash, n=n, base=base, buf=buf):
                self.assertEqual(game.determine_max_positions(cash, n, base, buf), want)

    def test_backtest_mirror_agrees(self):
        for cash, n, base, buf, _ in self.CASES:
            self.assertEqual(pb.expanded_max_positions(cash, n, base, buf),
                             game.determine_max_positions(cash, n, base, buf))

    def test_backtest_uses_the_live_constants(self):
        self.assertEqual(pb.SLOT_EXPAND_CASH, CFG.system_slot_expand_free_cash)
        self.assertEqual(pb.MIN_TICKET_USD, CFG.system_min_ticket_usd)


class TestMinimumTicket(unittest.TestCase):
    def _buy(self, balance, price):
        state = {"balance": balance, "equity": 10500.0, "positions": {}, "history": []}
        rules = {"scarcity_allocation_pct": 0.20, "max_allocation_pct": 0.15, "atr_multiplier": 2.5}
        top = [{"sym": "COMP", "price": price, "total": 13.4, "industry": "Real Estate", "bottom_desc": ""}]
        with mock.patch.object(game, "calculate_bubble_z_score", return_value=None), \
             mock.patch.object(game, "backtrack_verify", return_value=(True, "Verified")), \
             mock.patch.object(game, "check_failure_rules", return_value=(False, "")), \
             mock.patch.object(game.instruments, "is_scarcity_asset", return_value=False), \
             mock.patch.object(game.instruments, "is_fractional_eligible", return_value=False), \
             mock.patch("risk_utils.calculate_atr", return_value=0.5):
            n = game._execute_buys(state, top, 1, 2100.0, rules, "2026-11-03", "07:04", [],
                                   prices={"COMP": price})
        return n, state

    def test_dust_buy_is_skipped(self):
        # $2,116 cash - $2,100 buffer = $16 -> 1 share at $9.47 on main.
        n, state = self._buy(2116.0, 9.47)
        self.assertEqual(n, 0)
        self.assertNotIn("COMP", state["positions"])
        self.assertEqual(state["balance"], 2116.0)

    def test_normal_buy_still_fills(self):
        n, state = self._buy(3600.0, 9.47)
        self.assertEqual(n, 1)
        self.assertGreaterEqual(state["positions"]["COMP"]["qty"] * 9.47, CFG.system_min_ticket_usd)

    def test_small_scale_in_is_skipped(self):
        # A 0.05% loss cap ($5) allows 2 shares ($33) of the add: below the minimum.
        with mock.patch.object(CFG, "system_max_trade_risk_pct", 0.0005):
            state, adds = pyr._run("GNE", 16.685, {"qty": 20, "cost": 15.0, "stop_loss": 15.0,
                                                   "highest_close_since_acq": 16.685})
        self.assertEqual(state["positions"]["GNE"]["stop_loss"], 15.0, "the run moved the stop; fixture no longer tests the add")
        self.assertEqual(adds, [])
        self.assertEqual(state["positions"]["GNE"]["qty"], 20)

    def test_scale_in_above_the_minimum_still_fires(self):
        state, adds = pyr._run("GNE", 16.685, {"qty": 20, "cost": 15.0, "stop_loss": 15.0,
                                               "highest_close_since_acq": 16.685})
        self.assertEqual(len(adds), 1)


if __name__ == "__main__":
    unittest.main()
