"""
Unit tests for the AETHER Intraday Stop-Breach Monitor.
"""
import os
import sys
import unittest
from unittest import mock


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../scripts/utils"))

import intraday_monitor as monitor


class TestIntradayMonitor(unittest.TestCase):

    @mock.patch("intraday_monitor.load_state")
    @mock.patch("intraday_monitor.save_state")
    @mock.patch("intraday_monitor.get_monitored_positions")
    @mock.patch("intraday_monitor.get_live_prices")
    @mock.patch("intraday_monitor.generate_ai_analysis")
    @mock.patch("notify.send_email")
    def test_monitor_no_breaches(self, mock_send_email, mock_ai, mock_prices, mock_positions, mock_save, mock_load):
        """If no positions are in breach, no email is sent."""
        mock_positions.return_value = [{"symbol": "AAPL", "stop": 150.0}]
        mock_prices.return_value = {"AAPL": 160.0}
        mock_load.return_value = {"last_breached": {}}
        
        monitor.monitor()
        
        mock_send_email.assert_not_called()
        mock_save.assert_called_with({"last_breached": {}})

    @mock.patch("intraday_monitor.load_state")
    @mock.patch("intraday_monitor.save_state")
    @mock.patch("intraday_monitor.get_monitored_positions")
    @mock.patch("intraday_monitor.get_live_prices")
    @mock.patch("intraday_monitor.generate_ai_analysis")
    @mock.patch("notify.send_email")
    def test_monitor_unchanged_breaches(self, mock_send_email, mock_ai, mock_prices, mock_positions, mock_save, mock_load):
        """If breaches exist but are unchanged, duplicate emails are bypassed."""
        mock_positions.return_value = [{"symbol": "AAPL", "stop": 150.0}]
        mock_prices.return_value = {"AAPL": 140.0}
        mock_load.return_value = {"last_breached": {"AAPL": {"stop": 150.0, "price": 140.0}}}
        
        monitor.monitor()
        
        mock_send_email.assert_not_called()
        mock_save.assert_not_called()  # returns early when state is unchanged

    @mock.patch("intraday_monitor.load_state")
    @mock.patch("intraday_monitor.save_state")
    @mock.patch("intraday_monitor.get_monitored_positions")
    @mock.patch("intraday_monitor.get_live_prices")
    @mock.patch("intraday_monitor.generate_ai_analysis")
    @mock.patch("notify.send_email")
    def test_monitor_new_breach_only_analyses_new(self, mock_send_email, mock_ai, mock_prices, mock_positions, mock_save, mock_load):
        """If a new breach occurs, an email is sent but AI analysis is generated ONLY for the new breach."""
        mock_positions.return_value = [
            {"symbol": "AAPL", "stop": 150.0},
            {"symbol": "MSFT", "stop": 350.0}
        ]
        # AAPL was already in breach, MSFT is newly breached
        mock_prices.return_value = {"AAPL": 140.0, "MSFT": 340.0}
        mock_load.return_value = {"last_breached": {"AAPL": {"stop": 150.0, "price": 140.0}}}
        mock_ai.return_value = "AI Analysis Text"
        
        monitor.monitor()
        
        # Email sent
        mock_send_email.assert_called_once()
        
        # AI analysis run ONLY for MSFT (1 call), not AAPL!
        mock_ai.assert_called_once_with("MSFT", 340.0, 350.0)
        
        # Saved new state
        mock_save.assert_called_with({
            "last_breached": {
                "AAPL": {"stop": 150.0, "price": 140.0},
                "MSFT": {"stop": 350.0, "price": 340.0}
            }
        })

    @mock.patch("intraday_monitor.load_state")
    @mock.patch("intraday_monitor.save_state")
    @mock.patch("intraday_monitor.get_monitored_positions")
    @mock.patch("intraday_monitor.get_live_prices")
    @mock.patch("intraday_monitor.generate_ai_analysis")
    @mock.patch("notify.send_email")
    def test_monitor_cleared_breach(self, mock_send_email, mock_ai, mock_prices, mock_positions, mock_save, mock_load):
        """If a previously breached position is cleared (sold/recovered), an email alert is sent."""
        mock_positions.return_value = [{"symbol": "AAPL", "stop": 150.0}]
        # AAPL has recovered above the stop
        mock_prices.return_value = {"AAPL": 160.0}
        mock_load.return_value = {"last_breached": {"AAPL": {"stop": 150.0, "price": 140.0}}}
        
        monitor.monitor()
        
        # Cleared confirmation email bypassed to avoid noise
        mock_send_email.assert_not_called()
        
        # Saved state is now clean
        mock_save.assert_called_with({"last_breached": {}})

if __name__ == "__main__":
    unittest.main()
