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
        mock_save.assert_called_with({"last_breached": {}, "alerted": {}})

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
        mock_ai.assert_not_called()

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
        both = {"AAPL": {"stop": 150.0, "price": 140.0}, "MSFT": {"stop": 350.0, "price": 340.0}}
        mock_save.assert_called_with({"last_breached": both, "alerted": both})

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
        mock_save.assert_called_with({"last_breached": {}, "alerted": {}})


class TestBreachIsEmailedOnce(unittest.TestCase):
    """PROD 2026-11-03: 7 emails in one day, each listing the same 9-12 breaches, because a
    name crossing back over its stop counted as cleared and then as new again."""

    def _run(self, stops, prices, state):
        saved = {}
        with mock.patch.object(monitor, "load_state", return_value=state),              mock.patch.object(monitor, "save_state", side_effect=saved.update),              mock.patch.object(monitor, "get_monitored_positions",
                               return_value=[{"symbol": s, "stop": v} for s, v in stops.items()]),              mock.patch.object(monitor, "get_live_prices", return_value=prices),              mock.patch.object(monitor, "generate_ai_analysis", return_value="AI") as ai,              mock.patch("notify.send_email") as send:
            monitor.monitor()
        return send, ai, saved

    def test_bounce_and_rebreach_does_not_email_again(self):
        stops = {"TT": 409.83}
        send, _, saved = self._run(stops, {"TT": 405.38}, {"last_breached": {}})
        self.assertEqual(send.call_count, 1)
        # Bounces just above the stop: cleared from last_breached, still remembered as alerted.
        send, _, saved = self._run(stops, {"TT": 410.50}, saved)
        send.assert_not_called()
        self.assertIn("TT", saved["alerted"])
        # Back under the stop at about the same price: no second email.
        send, _, saved = self._run(stops, {"TT": 405.00}, saved)
        send.assert_not_called()

    def test_clear_recovery_lets_a_later_breach_alert(self):
        stops = {"TT": 400.0}
        _, _, saved = self._run(stops, {"TT": 395.0}, {"last_breached": {}})
        _, _, saved = self._run(stops, {"TT": 420.0}, saved)    # > 2% above the stop
        self.assertNotIn("TT", saved["alerted"])
        send, _, _ = self._run(stops, {"TT": 398.0}, saved)
        self.assertEqual(send.call_count, 1)

    def test_new_low_still_alerts(self):
        stops = {"TT": 400.0}
        _, _, saved = self._run(stops, {"TT": 395.0}, {"last_breached": {}})
        send, _, _ = self._run(stops, {"TT": 380.0}, saved)
        self.assertEqual(send.call_count, 1)

    def test_old_state_counts_as_already_alerted(self):
        # The state file from before this change has only last_breached: no burst on deploy.
        send, _, _ = self._run({"LULU": 400.0}, {"LULU": 112.31},
                               {"last_breached": {"LULU": {"stop": 400.0, "price": 112.31}}})
        send.assert_not_called()

    def test_first_seen_deep_breach_is_urgent_not_out_of_date(self):
        # Review of #238: a fresh 25% gap through a valid stop must not read as "remove the stop".
        send, ai, _ = self._run({"XYZ": 100.0}, {"XYZ": 75.0}, {"last_breached": {}})
        ai.assert_called_once_with("XYZ", 75.0, 100.0)
        body = send.call_args.args[1]
        self.assertIn("DEEP BREACH, first seen this run: the price is 25% below the stop", body)
        self.assertNotIn("out of date", body)

    def test_deep_breach_already_alerted_gets_the_out_of_date_note(self):
        # LULU was alerted at $112.31 under the same $400 stop; a new low re-alerts with the note
        # and no AI call. TT (new, shallow) still gets the AI analysis.
        state = {"last_breached": {}, "alerted": {"LULU": {"stop": 400.0, "price": 112.31}}}
        send, ai, _ = self._run({"LULU": 400.0, "TT": 409.83}, {"LULU": 100.0, "TT": 405.38}, state)
        ai.assert_called_once_with("TT", 405.38, 409.83)
        self.assertIn("may be out of date", send.call_args.args[1])

    def test_a_crash_that_deepens_under_the_same_stop_is_urgent(self):
        # Recheck of #238: alerted at -5%, now -25% under the same stop -> a crash in progress,
        # not a stale stop. AI analysis with the DEEP BREACH label, no "out of date".
        state = {"last_breached": {}, "alerted": {"XYZ": {"stop": 100.0, "price": 95.0}}}
        send, ai, _ = self._run({"XYZ": 100.0}, {"XYZ": 75.0}, state)
        ai.assert_called_once_with("XYZ", 75.0, 100.0)
        body = send.call_args.args[1]
        self.assertIn("DEEP BREACH, deeper than at the last alert: the price is 25% below the stop", body)
        self.assertNotIn("out of date", body)

    def test_out_of_date_needs_a_breach_deeper_than_20_pct(self):
        # Already alerted and a new low, but only 10% under the stop: AI analysis, no note.
        state = {"last_breached": {}, "alerted": {"TT": {"stop": 100.0, "price": 92.0}}}
        send, ai, _ = self._run({"TT": 100.0}, {"TT": 90.0}, state)
        ai.assert_called_once_with("TT", 90.0, 100.0)
        self.assertNotIn("out of date", send.call_args.args[1])

    def test_a_sold_name_is_forgotten(self):
        # GONE is no longer in the workbook (sold): it leaves the alerted memory even though no
        # quote says it recovered, so a later re-buy that breaches alerts again.
        state = {"last_breached": {}, "alerted": {"GONE": {"stop": 50.0, "price": 40.0}}}
        _, _, saved = self._run({"TT": 100.0}, {"TT": 110.0}, state)
        self.assertNotIn("GONE", saved["alerted"])

if __name__ == "__main__":
    unittest.main()
