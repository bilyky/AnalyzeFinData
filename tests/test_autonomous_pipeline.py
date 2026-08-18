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
