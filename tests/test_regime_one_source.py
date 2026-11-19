"""One trading regime (Development page F5): reports show the profile the game trades on.

2026-11-18/19: the status email said "Unknown" (a key get_system_health never set) while the
Autopilot email said "BULLISH" from SPY's Long60 alone, without the breadth step the game applies.
"""
import unittest
from unittest import mock

import ai_portfolio_game as game
import autonomous_pipeline as ap
import data_api
import workbook_read
from aether import regime


class TestRule(unittest.TestCase):
    def test_band(self):
        self.assertEqual([regime.base_profile(x) for x in (2.5, 2.0, 0, None, -2.0, -2.5)],
                         ["AGGRESSIVE", "BALANCED", "BALANCED", "BALANCED", "BALANCED", "DEFENSIVE"])

    def test_labels(self):
        self.assertEqual(regime.label("AGGRESSIVE")[0], "🚀 BULLISH (Risk-On)")
        self.assertEqual(regime.label("DEFENSIVE")[0], "⚠️ BEARISH (Risk-Off / Defensive)")
        self.assertEqual(regime.label("nonsense"), regime.UNKNOWN)


class TestReportsShowTheTradedRegime(unittest.TestCase):
    """SPY Long60 bullish, breadth narrow: the game trades BALANCED, so reports must say NEUTRAL."""

    def setUp(self):
        p = mock.patch.object(game, "get_market_regime", return_value="BALANCED")
        p.start()
        self.addCleanup(p.stop)

    def test_traded_label(self):
        self.assertEqual(game.get_market_regime_label()[0], "⚖️ NEUTRAL (Consolidation)")

    def test_dashboard_picks(self):
        with mock.patch.object(data_api, "_ap_picks", return_value=[]), \
                mock.patch.object(data_api, "_cached", side_effect=lambda k, ttl, f: f()):
            self.assertEqual(data_api.read_picks()["market_regime"], "⚖️ NEUTRAL (Consolidation)")

    def test_autopilot_email(self):
        with mock.patch.object(ap, "get_reserves_data", return_value=[]):
            html = ap.format_html_report("OK", [], [], [])
        self.assertIn("NEUTRAL (Consolidation)", html)
        self.assertNotIn("BULLISH (Risk-On)", html)

    def test_no_second_regime_copy(self):
        self.assertFalse(hasattr(workbook_read, "get_market_regime"))
        self.assertFalse(hasattr(data_api, "_ap_regime"))


if __name__ == "__main__":
    unittest.main()
