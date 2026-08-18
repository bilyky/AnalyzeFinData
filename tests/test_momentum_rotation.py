import unittest

import ai_portfolio_game as game
from aether import risk_utils
from aether.config import CFG


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
        """Adaptive s10 Floor (R&D #15): the production helper returns the stricter default
        floor under low cash drag and the relaxed floor once cash drag clears the threshold."""
        thr = CFG.system_cash_drag_threshold      # 25.0 default
        strict = CFG.system_default_s10_floor      # 2.5 default
        relaxed = CFG.system_adaptive_s10_floor    # 2.0 default
        # Below the drag threshold -> strict default floor
        self.assertEqual(game.adaptive_s10_floor(thr - 5.0), strict)
        # Exactly at the threshold is NOT above it -> still strict (boundary is exclusive)
        self.assertEqual(game.adaptive_s10_floor(thr), strict)
        # Above the drag threshold -> relaxed floor to deploy idle capital
        self.assertEqual(game.adaptive_s10_floor(thr + 5.0), relaxed)

    def test_high_score_pgr_bypass(self):
        """High-Score PGR Bypass (R&D #13/#32): the production gate qualifies an elite
        leader only when BOTH score and s10 clear their CFG floors, and rejects otherwise."""
        sf = CFG.system_bypass_score_floor  # 8.0 default
        s10f = CFG.system_bypass_s10_floor  # 2.0 default
        # Both above floor -> elite (waiver granted)
        self.assertTrue(risk_utils.is_elite_breakout_candidate(sf + 2.6, s10f + 1.6))
        # Exactly on both floors -> elite (>= boundary is inclusive)
        self.assertTrue(risk_utils.is_elite_breakout_candidate(sf, s10f))
        # Strong score but weak s10 -> NOT elite (both conditions required)
        self.assertFalse(risk_utils.is_elite_breakout_candidate(sf + 3.0, s10f - 0.1))
        # Strong s10 but weak score -> NOT elite
        self.assertFalse(risk_utils.is_elite_breakout_candidate(sf - 0.1, s10f + 3.0))

    def test_loosened_pyramiding(self):
        """Pyramiding momentum gate (R&D #31): the production helper scales into a winner
        when EITHER s10 holds its floor OR l60 shows strong trend support, but never when
        the position is not a peak-hugging winner."""
        s10f = CFG.system_pyramiding_s10_floor  # 0.0 default
        l60f = CFG.system_pyramiding_l60_floor  # 2.0 default
        # s10 holds -> pyramid
        self.assertTrue(game.should_pyramid_into_winner(True, True, s10f, l60f - 1.0))
        # s10 fails but strong long-term trend rescues it (pullback dip-buy)
        self.assertTrue(game.should_pyramid_into_winner(True, True, s10f - 0.5, l60f))
        # both momentum reads fail -> no add
        self.assertFalse(game.should_pyramid_into_winner(True, True, s10f - 0.5, l60f - 0.5))
        # not a winner, or not near peak -> never add regardless of momentum
        self.assertFalse(game.should_pyramid_into_winner(False, True, s10f + 5.0, l60f + 5.0))
        self.assertFalse(game.should_pyramid_into_winner(True, False, s10f + 5.0, l60f + 5.0))


if __name__ == '__main__':
    unittest.main()
