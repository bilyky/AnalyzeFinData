"""
Regression tests for autonomous_pipeline HTML report generation.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import autonomous_pipeline as ap


class TestFormatHtmlReport(unittest.TestCase):

    def test_intel_ideas_render_without_nameerror(self):
        """Regression: a non-empty intel_ideas list must render, not crash.

        The nested _e() escaper calls html.escape(); a local named `html` in
        format_html_report used to shadow the module for the whole function,
        so the FIRST _e() call (building the intel section, before the local
        was assigned) raised NameError. This exercises exactly that path.
        """
        intel = [{
            "from": "Analyst <Desk>",
            "subject": "Idea <TSLA>",
            "symbol": "TSLA",
            "sentiment": "BUY",
            "thesis": "Momentum & <breakout>",
        }]
        # No picks/reserves -> get_reasoning is never invoked; stub the workbook
        # readers so the test needs no live workbook.
        with mock.patch.object(ap, "get_market_regime", return_value=("NEUTRAL", "#000")), \
             mock.patch.object(ap, "get_reserves_data", return_value=[]):
            html_out = ap.format_html_report("OK", [], [], intel)

        self.assertIsInstance(html_out, str)
        self.assertIn("&lt;Desk&gt;", html_out)      # source HTML-escaped
        self.assertIn("&lt;TSLA&gt;", html_out)      # subject HTML-escaped
        self.assertIn("TSLA", html_out)              # symbol rendered
        self.assertIn("BUY", html_out)               # sentiment badge

    def test_empty_intel_renders_placeholder(self):
        """With no intel, the report still builds and shows the empty-feed notice."""
        with mock.patch.object(ap, "get_market_regime", return_value=("NEUTRAL", "#000")), \
             mock.patch.object(ap, "get_reserves_data", return_value=[]):
            html_out = ap.format_html_report("OK", [], [], [])
        self.assertIsInstance(html_out, str)
        self.assertIn("External Intelligence", html_out)


if __name__ == "__main__":
    unittest.main()


class TestMarketHoursPipelineDowngrade(unittest.TestCase):

    @mock.patch("autonomous_pipeline.is_market_hours")
    @mock.patch("autonomous_pipeline.run_preflight_diagnostics")
    @mock.patch("autonomous_pipeline.verify_data_freshness")
    @mock.patch("autonomous_pipeline.validate_sheets")
    @mock.patch("autonomous_pipeline.get_top_5_picks")
    @mock.patch("autonomous_pipeline.get_replacement_pairs")
    def test_market_hours_without_force_gracefully_downgrades(self, mock_rep, mock_picks, mock_val, mock_fresh, mock_pre, mock_is_market):
        """Regression: when is_market_hours() is True and no --force is present,
        the pipeline must gracefully downgrade to report-only/cached mode.
        """
        mock_is_market.return_value = True
        mock_fresh.return_value = (True, "OK")
        mock_val.return_value = (True, "OK")
        mock_picks.return_value = []
        mock_rep.return_value = []
        
        # Run main and ensure it doesn't run preflight (since it downgrades to report_only)
        with mock.patch("sys.exit") as mock_exit, \
             mock.patch("autonomous_pipeline.DailyRunGuard") as mock_guard, \
             mock.patch("autonomous_pipeline.notify.send_email") as mock_email, \
             mock.patch("autonomous_pipeline.watchdog.sync_data_folder") as mock_sync, \
             mock.patch("sys.argv", ["autonomous_pipeline.py"]):
            
            ap.main()
            
            # Since report_only was set, run_preflight_diagnostics must NOT have been called!
            mock_pre.assert_not_called()
