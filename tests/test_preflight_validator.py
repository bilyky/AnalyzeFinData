"""
Tests for preflight_validator.py weekend waiver logic.
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch


# Insert project root to import our local modules cleanly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Import the module to be tested
from scripts.diagnostics import preflight_validator


class TestPreflightValidatorWeekendWaiver(unittest.TestCase):
    @patch("aether.etrade.get_tokens")
    @patch("scripts.diagnostics.preflight_validator.datetime")
    def test_check_etrade_api_fails_on_weekday(self, mock_datetime, mock_get_tokens):
        # Setup: Mock a weekday (e.g. Monday, index 0)
        mock_now = MagicMock()
        mock_now.weekday.return_value = 0  # Monday
        mock_datetime.datetime.now.return_value = mock_now
        
        # Setup: E*TRADE verification fails
        mock_get_tokens.return_value = None
        
        # Action: Run the check
        res = preflight_validator.check_etrade_api()
        
        # Assertion: Should fail on weekdays
        self.assertFalse(res)

    @patch("aether.etrade.get_tokens")
    @patch("scripts.diagnostics.preflight_validator.datetime")
    def test_check_etrade_api_waived_on_weekend_none(self, mock_datetime, mock_get_tokens):
        # Setup: Mock a Saturday (index 5)
        mock_now = MagicMock()
        mock_now.weekday.return_value = 5  # Saturday
        mock_datetime.datetime.now.return_value = mock_now
        
        # Setup: E*TRADE verification fails by returning None
        mock_get_tokens.return_value = None
        
        # Action: Run the check
        res = preflight_validator.check_etrade_api()
        
        # Assertion: Should pass (waived) on weekends
        self.assertTrue(res)

    @patch("aether.etrade.get_tokens")
    @patch("scripts.diagnostics.preflight_validator.datetime")
    def test_check_etrade_api_waived_on_weekend_exception(self, mock_datetime, mock_get_tokens):
        # Setup: Mock a Sunday (index 6)
        mock_now = MagicMock()
        mock_now.weekday.return_value = 6  # Sunday
        mock_datetime.datetime.now.return_value = mock_now
        
        # Setup: E*TRADE verification raises an exception
        mock_get_tokens.side_effect = RuntimeError("Expired session")
        
        # Action: Run the check
        res = preflight_validator.check_etrade_api()
        
        # Assertion: Should pass (waived) on weekends even with an exception
        self.assertTrue(res)

    @patch("aether.etrade.get_tokens")
    def test_check_etrade_api_passes_when_tokens_valid(self, mock_get_tokens):
        # Setup: E*TRADE verification succeeds
        mock_get_tokens.return_value = {"oauth_token": "valid_token"}
        
        # Action: Run the check
        res = preflight_validator.check_etrade_api()
        
        # Assertion: Should always pass when tokens are valid
        self.assertTrue(res)
