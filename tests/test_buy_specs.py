"""R&D #113 loss filters (aether.buy_specs) and their use in the Oracle (SA, 2026-11-18)."""
import unittest
from unittest import mock

import aether_oracle as oracle
from aether import buy_specs as bs

SA_ROW = {"symbol": "SA", "setup": True, "s10": 6.9, "l60": 8.7, "price": 26.28, "stop": 23.03,
          "target": 26.76, "pgr": "Be", "patterns": "MACD+"}


def falling(n=300, top=39.35, bottom=24.5):
    """A slow fall from a 52-week high to 38% under it: SA's shape before 11-18."""
    return [top - (top - bottom) * k / (n - 1) for k in range(n)]


def near_high_uptrend(n=300):
    return [20.0 + 0.05 * k for k in range(n)]


class TestChecks(unittest.TestCase):
    def test_sa_shape_is_held_back_and_flagged(self):
        r = bs.loss_filter_reasons(True, falling())
        self.assertEqual(len(r), 1)
        self.assertRegex(r[0], r"^3\d% below its 52-week high, admitted only by the elite-breakout waiver")
        self.assertEqual(len(bs.loss_flags(falling())), 1)
        self.assertIn("falling 200-day", bs.loss_flags(falling())[0])

    def test_waiver_check_only_for_waiver_admits(self):
        self.assertEqual(bs.loss_filter_reasons(False, falling()), [])     # not a waiver admit: no hold-back
        self.assertTrue(bs.loss_flags(falling()))                            # but still flagged

    def test_a_leader_near_its_high_passes(self):
        self.assertEqual(bs.loss_filter_reasons(True, near_high_uptrend()), [])

    def test_ten_percent_is_the_line(self):
        f = {"below_high": 0.10, "below_falling_200": False}
        self.assertIsNone(bs.waiver_near_high(True, f))
        self.assertIsNotNone(bs.waiver_near_high(True, dict(f, below_high=0.1001)))

    def test_below_a_rising_200_day_is_not_flagged(self):
        c = near_high_uptrend() + [10.0]          # one sharp drop under a 200-day that is still rising
        f = bs.trend_features(c)
        self.assertLess(c[-1], sum(c[-200:]) / 200)
        self.assertFalse(f["below_falling_200"])
        self.assertIsNone(bs.not_below_falling_200(f))

    def test_short_history_does_not_block(self):
        self.assertEqual(bs.loss_filter_reasons(True, falling(100)), [])
        self.assertIsNone(bs.trend_features([]))


class TestOracle(unittest.TestCase):
    def candidates(self, rows, closes):
        held = []
        with mock.patch.object(oracle.data_api, "read_research", return_value={"rows": rows}), \
                mock.patch.object(oracle.ai_portfolio_game, "calculate_bubble_z_score", return_value=0.0), \
                mock.patch.object(oracle.risk_utils, "_load_ohlcv_series", return_value=([], [], closes, None)):
            return oracle.get_oracle_buy_candidates({"holdings": []}, held), held

    def test_sa_is_held_back_and_said_so(self):
        buys, held = self.candidates([SA_ROW], falling())
        self.assertEqual(buys, [])
        self.assertEqual(held[0]["symbol"], "SA")
        html = oracle.generate_oracle_html({"holdings": [], "equity": 1.0, "balance": 0.0, "id": "0000"},
                                           [], [], buys, held)
        self.assertIn("Held back by the loss filters (R&D #113):", html)
        self.assertRegex(html, r"SA: 3\d% below its 52-week high")

    def test_a_good_rr_idea_below_a_falling_200_day_is_kept_and_flagged(self):
        row = dict(SA_ROW, symbol="OK1", target=33.0)          # reward/risk 2.07: no waiver
        buys, held = self.candidates([row], falling())
        self.assertEqual(held, [])
        self.assertIn("falling 200-day", buys[0]["flags"][0])
        self.assertIn("⚠️ below a falling 200-day", oracle._checked_note(buys[0]))

    def test_a_leader_near_its_high_still_gets_the_waiver(self):
        buys, held = self.candidates([SA_ROW], near_high_uptrend())
        self.assertEqual([b["symbol"] for b in buys], ["SA"])
        self.assertEqual(held, [])


if __name__ == "__main__":
    unittest.main()
