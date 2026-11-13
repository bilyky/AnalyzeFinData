"""The stop monitor says once when it has no prices, instead of a warning per symbol.

PROD 2026-11-09 11:15-12:16: DNS failed on the PROD box (getaddrinfo), E*TRADE returned no quotes,
and every 30-min run logged one "Could not fetch live price" warning per position (about 44 a run)
and no alert said the monitor was blind (PROD review 2026-11-12).
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "utils"))
import intraday_monitor as monitor

_STOPS = {f"S{i}": 10.0 for i in range(44)}


def _run(prices, state, today="2026-11-09"):
    saved = {}
    with mock.patch.object(monitor, "load_state", return_value=state), \
         mock.patch.object(monitor, "save_state", side_effect=saved.update), \
         mock.patch.object(monitor, "get_monitored_positions",
                           return_value=[{"symbol": s, "stop": v} for s, v in _STOPS.items()]), \
         mock.patch.object(monitor, "get_live_prices", return_value=prices), \
         mock.patch.object(monitor, "generate_ai_analysis", return_value="AI"), \
         mock.patch.object(monitor, "_today", return_value=today), \
         mock.patch("notify.send_email") as send:
        with mock.patch.object(monitor._log, "warning") as warn:
            monitor.monitor()
    return send, warn, saved


class MonitorBlindTest(unittest.TestCase):
    def test_no_prices_is_one_warning_and_one_email(self):
        send, warn, saved = _run({}, {"last_breached": {}})
        price_warnings = [c for c in warn.call_args_list if "live price" in str(c)]
        self.assertEqual(len(price_warnings), 1)
        self.assertIn("44/44", str(price_warnings[0]))
        self.assertEqual(send.call_count, 1)
        self.assertIn("no prices", send.call_args.args[0].lower())
        self.assertEqual(saved["blind_alerted"], "2026-11-09")

    def test_the_blind_email_goes_once_a_day(self):
        _, _, saved = _run({}, {"last_breached": {}})
        send, _, saved = _run({}, saved)
        send.assert_not_called()
        send, _, _ = _run({}, saved, today="2026-11-10")
        self.assertEqual(send.call_count, 1)

    def test_a_few_missing_quotes_warn_once_without_an_email(self):
        prices = {s: 12.0 for s in list(_STOPS)[:40]}
        send, warn, _ = _run(prices, {"last_breached": {}})
        price_warnings = [c for c in warn.call_args_list if "live price" in str(c)]
        self.assertEqual(len(price_warnings), 1)
        self.assertIn("4/44", str(price_warnings[0]))
        send.assert_not_called()

    def test_a_missing_quote_does_not_clear_a_known_breach(self):
        state = {"last_breached": {"S1": {"stop": 10.0, "price": 9.0}},
                 "alerted": {"S1": {"stop": 10.0, "price": 9.0}}}
        prices = {s: 12.0 for s in _STOPS if s != "S1"}
        send, _, saved = _run(prices, state)
        self.assertIn("S1", saved["last_breached"])        # unknown is not "cleared"
        self.assertIn("S1", saved["alerted"])
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
