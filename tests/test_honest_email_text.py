"""Emails state what AETHER checked, not AI-invented facts (2026-11-19 email review).

- The Autopilot's "Catalyst" sentence was invented from five numbers (SA on 11-18: "a pending
  acquisition of a high-grade ore deposit"; a court ruling sank SA 29% the next day).
- The stop-breach "AI Risk Analysis" invented causes and numbers ("0.31 % below the floor" for a
  0.04% breach) and said "close the position immediately", also on the dated plan's SPY.
- The Oracle printed "Confirmed bottom setup" for every buy idea; nothing checks for a bottom, and
  SA got in only through the elite-breakout waiver (reward/risk 0.15 against a 1.5 minimum).
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "utils"))

import aether_oracle as oracle
import external_intel
import intraday_monitor as monitor

# The Oracle's SA row on 2026-11-18 (entry, stop and target from that morning's email).
SA_ROW = {"symbol": "SA", "setup": True, "s10": 6.9, "l60": 8.7, "price": 26.28, "stop": 23.03,
          "target": 26.76, "pgr": "Be", "patterns": "MACD+"}


class TestNoInventedCatalyst(unittest.TestCase):
    def test_only_one_skeptical_sentence_and_no_catalyst(self):
        reply = ("Thin margins in a cyclical market could erode earnings. "
                 "However, a pending acquisition of a high-grade ore deposit acts as a catalyst.")
        with mock.patch.object(external_intel.ai_client, "evaluate", return_value=reply) as ev:
            out = external_intel.get_ai_reasoning("SA", "Metals & Mining", "Be", 6.9, 8.7)
        self.assertNotIn("Catalyst", out)
        self.assertNotIn("acquisition", out)
        self.assertIn("Thin margins in a cyclical market could erode earnings.", out)
        self.assertIn("no news read", out)
        prompt = ev.call_args[0][1]
        self.assertNotIn("Catalyst", prompt)
        self.assertIn("do not state any fact about the company", prompt)


class TestBreachNoteIsFacts(unittest.TestCase):
    def test_spy_breach_of_four_hundredths_is_stated_as_such(self):
        note = monitor.breach_note("SPY", 719.22, 719.53)
        self.assertIn("SPY at $719.22 is 0.04% below its workbook stop of $719.53", note)
        self.assertIn("is 10.00% below", monitor.breach_note("X", 90.0, 100.0))
        self.assertNotIn("immediately", note.lower())
        self.assertNotIn("0.31", note)

    def test_the_alert_does_not_call_an_ai(self):
        self.assertFalse(hasattr(monitor, "ai_client"))
        self.assertFalse(hasattr(monitor, "generate_ai_analysis"))

    def test_the_email_body_carries_the_note(self):
        with mock.patch.object(monitor, "load_state", return_value={"last_breached": {}}), \
                mock.patch.object(monitor, "save_state"), \
                mock.patch.object(monitor, "get_monitored_positions", return_value=[{"symbol": "SPY", "stop": 719.53}]), \
                mock.patch.object(monitor, "get_live_prices", return_value={"SPY": 719.22}), \
                mock.patch.object(monitor.notify, "send_email") as send:
            monitor.monitor()
        body = send.call_args[0][1]
        self.assertIn("0.04% below its workbook stop", body)
        self.assertNotIn("AI Risk Analysis", body)


class TestOracleSaysWhatItChecked(unittest.TestCase):
    def candidates(self, rows):
        with mock.patch.object(oracle.data_api, "read_research", return_value={"rows": rows}), \
                mock.patch.object(oracle.ai_portfolio_game, "calculate_bubble_z_score", return_value=0.0):
            return oracle.get_oracle_buy_candidates({"holdings": []})

    def test_sa_is_marked_as_a_waiver_admit(self):
        (c,) = self.candidates([SA_ROW])
        self.assertAlmostEqual(c["rr"], 0.148, places=3)
        self.assertTrue(c["rr_waived"])
        note = oracle._checked_note(c)
        self.assertIn("reward/risk 0.15 (under the minimum: admitted only by the elite-breakout waiver)", note)
        self.assertIn("Not checked: news, earnings, legal or regulatory events", note)

    def test_a_normal_admit_is_not_marked(self):
        row = dict(SA_ROW, symbol="OK1", target=33.0)          # reward 6.72 vs risk 3.25: 2.07
        (c,) = self.candidates([row])
        self.assertFalse(c["rr_waived"])
        self.assertNotIn("waiver", oracle._checked_note(c))

    def test_the_email_has_no_confirmed_bottom_claim(self):
        (c,) = self.candidates([SA_ROW])
        acct = {"holdings": [], "equity": 1.0, "balance": 0.0, "id": "0000"}
        html = oracle.generate_oracle_html(acct, [], [], [c])
        self.assertNotIn("Confirmed bottom", html)
        self.assertIn("elite-breakout waiver", html)


class TestNeverRunIsNotMissed(unittest.TestCase):
    """The 11-18 status email counted three tasks 'missed' that had simply never run."""

    def test_task_scheduler_never_run_value(self):
        import data_api
        self.assertEqual(data_api._last_run_text("11/30/1999 12:00:00 AM", "267011"), "never run")
        self.assertEqual(data_api._last_run_text("11/30/1999 12:00:00 AM", ""), "never run")
        self.assertEqual(data_api._last_run_text("11/18/2026 2:00:01 PM", "0"), "11/18/2026 2:00:01 PM")

    def test_live_states_use_it(self):
        import data_api
        csv = ('"HostName","TaskName","Next Run Time","Status","Last Run Time","Last Result"\n'
               '"PC","\\AETHER\\AETHER_StatusReport","N/A","Ready","11/30/1999 12:00:00 AM","267011"\n'
               '"PC","\\AETHER\\AETHER_Morning","N/A","Ready","11/19/2026 5:45:00 AM","0"\n')
        with mock.patch.object(data_api.subprocess, "check_output", return_value=csv):
            states = data_api._live_task_states("\\AETHER")
        self.assertEqual(states["AETHER_StatusReport"]["last_run"], "never run")
        self.assertEqual(states["AETHER_Morning"]["last_run"], "11/19/2026 5:45:00 AM")


if __name__ == "__main__":
    unittest.main()
