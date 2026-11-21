"""short_score's `waiver` argument (R&D #112 study).

The default ("bull_or_setup") must score exactly as before the argument existed; GOLDEN was
captured from short_score on origin/main (4d70925) before the change. "bull_only" is the rule
the live game ran before f05e685 (2026-08-24); "none" never waives.
"""
import itertools
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aether.scoring import short_score

GRID = list(itertools.product(["Bull", "Neutral", "Bear"], [True, False, None], ["Strong", "Weak"],
                              ["Strong", "Weak"], [2.0, -2.0]))

GOLDEN = [5.0, 5.3, 6.5, 6.8, 7.0, 7.3, 8.5, 8.8, 5.0, 5.3, 6.5, 6.8, 7.0, 7.3, 8.5, 8.8, 5.0, 5.3, 6.5, 6.8,
          7.0, 7.3, 8.5, 8.8, 4.0, 4.3, 5.5, 5.8, 6.0, 6.3, 7.5, 7.8, -0.4, 0.8, 2.6, 3.8, 3.6, 4.8, 6.6, 7.8,
          -0.4, 0.8, 2.6, 3.8, 3.6, 4.8, 6.6, 7.8, 3.0, 3.3, 4.5, 4.8, 5.0, 5.3, 6.5, 6.8, -1.4, -0.2, 1.6, 2.8,
          2.6, 3.8, 5.6, 6.8, -1.4, -0.2, 1.6, 2.8, 2.6, 3.8, 5.6, 6.8]


def _fields(regime, setup_ok, industry, lt, mom):
    return {"rel_vol": "High", "ob_os": "Early", "money_flow": "Neutral", "market_regime": regime,
            "setup_ok": setup_ok, "industry_strength": industry, "lt_trend": lt, "momentum_score": mom,
            "seasonality": 0.5, "chart_score": 1.0}


class TestShortScoreWaiver(unittest.TestCase):
    def test_default_matches_pre_change_scores(self):
        got = [short_score(_fields(*g)) for g in GRID]
        self.assertEqual(got, GOLDEN)

    def test_explicit_bull_or_setup_equals_default(self):
        for g in GRID:
            self.assertEqual(short_score(_fields(*g), waiver="bull_or_setup"), short_score(_fields(*g)))

    def test_bull_only_ignores_setup(self):
        # Setup alone no longer waives: Neutral + setup_ok True scores like Neutral + setup_ok False.
        f_setup = _fields("Neutral", True, "Strong", "Strong", 2.0)
        f_plain = _fields("Neutral", False, "Strong", "Strong", 2.0)
        self.assertEqual(short_score(f_setup, waiver="bull_only"), short_score(f_plain))
        self.assertEqual(short_score(f_setup, waiver="bull_only"), -0.4)
        self.assertEqual(short_score(f_setup), 4.0)                     # current rule waives
        # Bull still waives under bull_only.
        f_bull = _fields("Bull", False, "Strong", "Strong", 2.0)
        self.assertEqual(short_score(f_bull, waiver="bull_only"), short_score(f_bull))
        self.assertEqual(short_score(f_bull, waiver="bull_only"), 5.0)

    def test_none_never_waives(self):
        # Bull + setup: industry Strong -2, lt Strong -1.5, momentum +2 * -0.30 = -0.6.
        f = _fields("Bull", True, "Strong", "Strong", 2.0)
        # base: High 2.5 + Early 1 + seas 0.5 + Bull 1 + chart 1.0 * -0.30 = 4.7
        self.assertEqual(short_score(f, waiver="none"), round(4.7 - 2.0 - 1.5 - 0.6, 1))
        # Weak industry / trend score the same in every mode (only Strong is waived).
        f_weak = _fields("Bull", True, "Weak", "Weak", -2.0)
        self.assertEqual({short_score(f_weak, waiver=m) for m in ("none", "bull_only", "bull_or_setup")},
                         {short_score(f_weak)})

    def test_modes_ordered_on_grid(self):
        # Waiving only removes penalties, so none <= bull_only <= bull_or_setup on every row.
        for g in GRID:
            f = _fields(*g)
            self.assertLessEqual(short_score(f, waiver="none"), short_score(f, waiver="bull_only"))
            self.assertLessEqual(short_score(f, waiver="bull_only"), short_score(f, waiver="bull_or_setup"))

    def test_unknown_mode_raises(self):
        with self.assertRaises(ValueError):
            short_score(_fields("Bull", True, "Strong", "Strong", 2.0), waiver="setup_only")


if __name__ == "__main__":
    unittest.main()
