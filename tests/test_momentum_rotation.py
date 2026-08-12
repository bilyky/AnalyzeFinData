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

if __name__ == '__main__':
    unittest.main()
