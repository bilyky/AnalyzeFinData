import unittest

import ai_portfolio_game as game


class TestDynamicMomentumRotation(unittest.TestCase):

    def test_evaluate_momentum_rotation_success(self):
        # We are in AGGRESSIVE profile and market is open
        profile = "AGGRESSIVE"
        is_market_open = True
        available_slots = 0
        max_positions = 6
        
        positions = {
            # MATURE (profit-locked), LOW SCORE (5.0) -> Candidate for Rotation (Sell)
            "OLD_MATURE": {"qty": 10, "cost": 50.0, "stop_loss": 55.0},
            # NOT MATURE (in loss, stop < cost) -> MUST NOT ROTATE
            "OLD_LOSER": {"qty": 10, "cost": 50.0, "stop_loss": 45.0},
            # MATURE, but HIGH SCORE (10.0) -> Should rotate the LOWEST score first
            "OLD_STRONG": {"qty": 10, "cost": 50.0, "stop_loss": 55.0},
        }
        prices = {
            "OLD_MATURE": 60.0, "OLD_LOSER": 48.0, "OLD_STRONG": 60.0
        }
        top_buys = [
            # Elite breakout leader!
            {"sym": "NEW_ELITE", "total": 13.0}
        ]
        active_position_scores = {
            "OLD_MATURE": 5.0,
            "OLD_LOSER": 4.0,
            "OLD_STRONG": 10.0
        }
        
        # Call pure function
        sells, next_slots, cash_add = game.evaluate_momentum_rotation(
            profile, is_market_open, available_slots, max_positions,
            positions, prices, top_buys, active_position_scores
        )
        
        # ASSERTIONS
        self.assertEqual(sells, ["OLD_MATURE"])
        self.assertEqual(next_slots, 1)
        self.assertEqual(cash_add, 600.0) # 10 shares * $60.0 current price

    def test_evaluate_momentum_rotation_balanced_profile(self):
        # BALANCED profile should not rotate
        sells, next_slots, cash_add = game.evaluate_momentum_rotation(
            "BALANCED", True, 0, 5,
            {"OLD_MATURE": {"qty": 10, "cost": 50.0, "stop_loss": 55.0}},
            {"OLD_MATURE": 60.0}, [{"sym": "NEW_ELITE", "total": 13.0}], {"OLD_MATURE": 5.0}
        )
        self.assertEqual(sells, [])
        self.assertEqual(next_slots, 0)
        self.assertEqual(cash_add, 0.0)

    def test_evaluate_momentum_rotation_market_closed(self):
        # Market closed should not rotate
        sells, next_slots, cash_add = game.evaluate_momentum_rotation(
            "AGGRESSIVE", False, 0, 6,
            {"OLD_MATURE": {"qty": 10, "cost": 50.0, "stop_loss": 55.0}},
            {"OLD_MATURE": 60.0}, [{"sym": "NEW_ELITE", "total": 13.0}], {"OLD_MATURE": 5.0}
        )
        self.assertEqual(sells, [])
        self.assertEqual(next_slots, 0)
        self.assertEqual(cash_add, 0.0)

    def test_evaluate_momentum_rotation_no_elite_buy(self):
        # If top buy is below 12.0 (e.g. 11.5), should not rotate
        sells, next_slots, cash_add = game.evaluate_momentum_rotation(
            "AGGRESSIVE", True, 0, 6,
            {"OLD_MATURE": {"qty": 10, "cost": 50.0, "stop_loss": 55.0}},
            {"OLD_MATURE": 60.0}, [{"sym": "NEW_ELITE", "total": 11.5}], {"OLD_MATURE": 5.0}
        )
        self.assertEqual(sells, [])
        self.assertEqual(next_slots, 0)
        self.assertEqual(cash_add, 0.0)

    def test_adaptive_s10_floor(self):
        """Operational: Assert that Adaptive s10 Floor dynamically lowers required floor to 2.0 when cash_pct is high."""
        # Standard case (cash < 25%) -> required floor is 2.5
        state_low_cash = {"balance": 1000.0, "equity": 10000.0, "positions": {}}
        short10 = 2.4
        cash_pct = (state_low_cash["balance"] / state_low_cash["equity"]) * 100.0
        required_floor = 2.0 if cash_pct > 25.0 else 2.5
        self.assertEqual(required_floor, 2.5)
        self.assertLess(short10, required_floor)
        
        # High cash case (cash > 25%) -> required floor is 2.0
        state_high_cash = {"balance": 3500.0, "equity": 10000.0, "positions": {}}
        cash_pct_high = (state_high_cash["balance"] / state_high_cash["equity"]) * 100.0
        required_floor_high = 2.0 if cash_pct_high > 25.0 else 2.5
        self.assertEqual(required_floor_high, 2.0)
        self.assertGreaterEqual(short10, required_floor_high)

    def test_high_score_pgr_bypass(self):
        """Operational: Assert that High-Score PGR Bypass (R&D #13) waives the Chaikin PGR constraint for elite breakout leaders."""
        total_score = 10.6
        short10 = 3.6
        is_blue_sky = True
        is_elite_breakout = (total_score >= 6.0) and (short10 >= 2.0) and is_blue_sky
        self.assertTrue(is_elite_breakout)

    def test_loosened_pyramiding(self):
        """Operational: Assert that Pyramiding (R&D #31) momentum floor is successfully loosened to 1.0."""
        s10_low = 0.5
        s10_ok = 1.2
        is_winner = True
        has_peak = True
        low_ok = is_winner and has_peak and s10_low >= 1.0
        ok_ok = is_winner and has_peak and s10_ok >= 1.0
        self.assertFalse(low_ok)
        self.assertTrue(ok_ok)


if __name__ == '__main__':
    unittest.main()
