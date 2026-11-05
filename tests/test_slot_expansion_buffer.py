"""Slot expansion counts the cash buffer (PROD review 2026-11-04).

On PROD 2026-11-03 the game held 10 positions with 20.1% cash under BALANCED (20% buffer).
Slot expansion saw "cash > 15%" and opened an 11th slot, but only ~$16 sat above the buffer,
so the slot bought 1 share of COMP at $9.47. Expansion now needs 15% of equity *above the
buffer*. (A minimum position size is R&D #65's question: INCONCLUSIVE, not wired.)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
import ai_portfolio_game as game
import portfolio_backtest as pb
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

    def test_backtest_uses_the_live_constant(self):
        self.assertEqual(pb.SLOT_EXPAND_CASH, CFG.system_slot_expand_free_cash)


if __name__ == "__main__":
    unittest.main()
