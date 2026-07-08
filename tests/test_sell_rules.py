"""
Tests for sell_rules.py — the unified deterministic exit policy.
The hard-stop floor and winner-protection are risk-critical; cover them thoroughly.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import sell_rules as sr


class TestStatusLabel(unittest.TestCase):
    def test_thresholds(self):
        cases = [(9, "STRONG HOLD"), (4, "STRONG HOLD"), (3.9, "HOLD"), (2, "HOLD"),
                 (1.9, "WATCH"), (0, "WATCH"), (-0.1, "REDUCE"), (-2, "REDUCE"),
                 (-2.1, "EXIT"), (-8, "EXIT")]
        for l60, expected in cases:
            with self.subTest(l60=l60):
                self.assertEqual(sr.status_label(l60), expected)

    def test_none_is_na(self):
        self.assertEqual(sr.status_label(None), "N/A")


class TestSoftExit(unittest.TestCase):
    def test_negative_sum_exits(self):
        self.assertTrue(sr.soft_exit(-1, -1))
        self.assertTrue(sr.soft_exit(2, -3))

    def test_zero_or_positive_holds(self):
        self.assertFalse(sr.soft_exit(0, 0))
        self.assertFalse(sr.soft_exit(3, -2))   # sum +1
        self.assertFalse(sr.soft_exit(5, 5))

    def test_none_scores_treated_as_zero(self):
        self.assertFalse(sr.soft_exit(None, None))
        self.assertTrue(sr.soft_exit(None, -1))


class TestSma(unittest.TestCase):
    def test_sma_of_closes(self):
        self.assertEqual(sr.sma_from_closes([10.0] * 50), 10.0)
        self.assertAlmostEqual(sr.sma_from_closes(list(range(1, 51))), 25.5)

    def test_insufficient_returns_none(self):
        self.assertIsNone(sr.sma_from_closes([1, 2, 3], period=50))
        self.assertIsNone(sr.sma_from_closes([], period=50))

    def test_uses_last_period(self):
        # 60 closes; SMA50 uses the last 50 (11..60), mean = 35.5
        self.assertAlmostEqual(sr.sma_from_closes(list(range(1, 61))), 35.5)


class TestWinnerProtected(unittest.TestCase):
    def test_winner_above_sma_protected(self):
        self.assertTrue(sr.winner_protected(in_profit=True, price=110, sma50=100))

    def test_winner_below_sma_not_protected(self):
        self.assertFalse(sr.winner_protected(in_profit=True, price=95, sma50=100))

    def test_loser_never_protected(self):
        self.assertFalse(sr.winner_protected(in_profit=False, price=110, sma50=100))

    def test_missing_sma_not_protected(self):
        self.assertFalse(sr.winner_protected(in_profit=True, price=110, sma50=None))


class TestExitDecision(unittest.TestCase):
    def test_hard_stop_beats_everything(self):
        # Even a strong winner: if price <= stop, SELL (capital preservation).
        action, reason = sr.exit_decision(price=90, cost=50, stop_loss=95,
                                          s10=8, l60=9, sma50=80)
        self.assertEqual(action, "SELL")
        self.assertIn("stop", reason.lower())

    def test_stop_wins_even_when_winner_protected_would_apply(self):
        # In profit + above 50-DMA would normally be REVIEW, but stop breach overrides.
        action, _ = sr.exit_decision(price=99, cost=50, stop_loss=100,
                                     s10=-5, l60=-5, sma50=60)
        self.assertEqual(action, "SELL")

    def test_soft_exit_loser_sells(self):
        action, reason = sr.exit_decision(price=40, cost=50, stop_loss=30,
                                          s10=-2, l60=-2, sma50=45)
        self.assertEqual(action, "SELL")
        self.assertIn("momentum", reason.lower())

    def test_soft_exit_winner_above_sma_reviews(self):
        # ANET/RPD case: in profit, above 50-DMA, soft signal negative -> REVIEW not SELL.
        action, reason = sr.exit_decision(price=178, cost=171, stop_loss=130,
                                          s10=-2.6, l60=-4.1, sma50=170)
        self.assertEqual(action, "REVIEW")
        self.assertIn("review", reason.lower())

    def test_soft_exit_winner_below_sma_sells(self):
        action, _ = sr.exit_decision(price=172, cost=171, stop_loss=130,
                                     s10=-2, l60=-3, sma50=180)
        self.assertEqual(action, "SELL")

    def test_healthy_position_holds(self):
        action, reason = sr.exit_decision(price=120, cost=100, stop_loss=90,
                                          s10=3, l60=5, sma50=110)
        self.assertEqual(action, "HOLD")
        self.assertEqual(reason, "")

    def test_no_stop_configured_still_soft_sells(self):
        action, _ = sr.exit_decision(price=40, cost=50, stop_loss=0,
                                     s10=-3, l60=-3, sma50=None)
        self.assertEqual(action, "SELL")

    def test_enph_style_deep_loser_not_protected(self):
        # Deeply underwater, soft-negative, below its 50-DMA -> SELL (not a "flower").
        action, _ = sr.exit_decision(price=42, cost=141, stop_loss=33,
                                     s10=-4.5, l60=-6, sma50=55)
        self.assertEqual(action, "SELL")

    def test_in_profit_derived_from_price_vs_cost(self):
        # No explicit in_profit; price>cost + above sma + soft-negative -> REVIEW
        action, _ = sr.exit_decision(price=110, cost=100, stop_loss=80,
                                     s10=-1, l60=-1, sma50=105)
        self.assertEqual(action, "REVIEW")


if __name__ == "__main__":
    unittest.main()
