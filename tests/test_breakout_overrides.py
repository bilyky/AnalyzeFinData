import unittest
import sys
import os
import json

# Insert dev worktree path to import the modified local modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aether import scoring
import ai_portfolio_game

class TestScoringRegimeConditionalPenalties(unittest.TestCase):
    def test_contrarian_penalties_applied_on_bearish_neutral_regimes(self):
        # Case A: Market is Neutral, no confirmed setup (standard defensive mode)
        fields = {
            "rel_vol": "Neutral",
            "ob_os": "Neutral",
            "money_flow": "Neutral",
            "industry_strength": "Strong",  # penalty should be -2.0
            "lt_trend": "Strong",          # penalty should be -1.5
            "seasonality": 0.0,
            "market_regime": "Neutral",
            "fibonacci": 0.0,
            "rsi_divergence": 0.0,
            "candlestick_score": 0.0,
            "chart_score": 0.0,
            "momentum_score": 2.0,          # penalty should be 2.0 * -0.30 = -0.6
            "digit_sum": 0.0,
            "vrecovery_score": 0.0
        }
        score = scoring.short_score(fields)
        # Expected base score: -2.0 (ind_strength) + -1.5 (lt_trend) + 0.0 (regime) + -0.6 (momentum) = -4.1
        self.assertEqual(score, -4.1)

    def test_contrarian_penalties_waived_on_bullish_or_setup_breakouts(self):
        # Case B: Market is in a Bull regime (breakout strength mode)
        fields = {
            "rel_vol": "Neutral",
            "ob_os": "Neutral",
            "money_flow": "Neutral",
            "industry_strength": "Strong",  # penalty should be 0.0 (waived!)
            "lt_trend": "Strong",          # penalty should be 0.0 (waived!)
            "seasonality": 0.0,
            "market_regime": "Bull",        # regime adds +1.0
            "fibonacci": 0.0,
            "rsi_divergence": 0.0,
            "candlestick_score": 0.0,
            "chart_score": 0.0,
            "momentum_score": 2.0,          # positive momentum boost: 2.0 * 0.15 = +0.3 (waived penalty!)
            "digit_sum": 0.0,
            "vrecovery_score": 0.0
        }
        score = scoring.short_score(fields)
        # Expected score: 0.0 (ind_strength) + 0.0 (lt_trend) + 1.0 (regime) + 0.3 (momentum) = 1.3
        self.assertEqual(score, 1.3)

        # Case C: Market is Neutral but a confirmed breakout setup exists.
        # The dict key populated by powergauge._compute_pgr_fields is 'setup_ok'
        # (bool), NOT 'setup'. This case exercises the setup-triggered waiver and
        # would fail against the pre-fix code that read a non-existent 'setup' key.
        fields["market_regime"] = "Neutral"
        fields["setup_ok"] = True
        score = scoring.short_score(fields)
        # Expected score: 0.0 (ind_strength) + 0.0 (lt_trend) + 0.0 (regime) + 0.3 (momentum) = 0.3
        self.assertEqual(score, 0.3)

        # Case D (negative gate): Neutral regime with setup_ok=False must NOT waive —
        # contrarian penalties stay on. Locks the gate against a False/None setup.
        fields["setup_ok"] = False
        score = scoring.short_score(fields)
        # Expected: -2.0 (ind_strength) + -1.5 (lt_trend) + 0.0 (regime) + -0.6 (momentum) = -4.1
        self.assertEqual(score, -4.1)


class TestPGRWaiverRules(unittest.TestCase):
    def setUp(self):
        # Create a temporary failure_dna_rules.json inside the dev Data folder for testing
        self.rules_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Data")
        os.makedirs(self.rules_dir, exist_ok=True)
        self.rules_path = os.path.join(self.rules_dir, "failure_dna_rules.json")
        self.had_rules = os.path.exists(self.rules_path)
        if self.had_rules:
            self.old_content = open(self.rules_path, "r", encoding="utf-8").read()
            
        test_rules = [
            {
                "field": "pgr",
                "condition": "startswith_Be",
                "reason": "Toxic Bearish PGR"
            }
        ]
        with open(self.rules_path, "w", encoding="utf-8") as f:
            json.dump(test_rules, f)

    def tearDown(self):
        # Restore old rules file if it existed, else delete the test file
        if self.had_rules:
            with open(self.rules_path, "w", encoding="utf-8") as f:
                f.write(self.old_content)
        else:
            if os.path.exists(self.rules_path):
                os.remove(self.rules_path)

    def test_pgr_waiver_rules_bypassed_on_high_conviction(self):
        # Mock is_bottom_confirmed to return False
        def mock_is_bottom_confirmed(sym):
            return False, "Not confirmed"
        
        original_bottom = ai_portfolio_game.is_bottom_confirmed
        ai_portfolio_game.is_bottom_confirmed = mock_is_bottom_confirmed
        
        try:
            # Case A: Bearish PGR with high combined score (score >= 10.0) -> Bypassed (returns False, not blocked)
            is_toxic, reason = ai_portfolio_game.check_failure_rules(
                symbol="TEST_HIGH", pgr="Bearish", score=10.5, z_score=1.0, industry="Utilities"
            )
            self.assertFalse(is_toxic)
            
            # Case B: Bearish PGR with low combined score (score < 10.0) -> Blocked (returns True)
            is_toxic, reason = ai_portfolio_game.check_failure_rules(
                symbol="TEST_LOW", pgr="Bearish", score=4.5, z_score=1.0, industry="Utilities"
            )
            self.assertTrue(is_toxic)
            self.assertIn("Toxic", reason)
        finally:
            ai_portfolio_game.is_bottom_confirmed = original_bottom


if __name__ == "__main__":
    unittest.main()
