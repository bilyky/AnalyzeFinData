"""PROD review 2026-11-22 fixes.

1. The intraday monitor does not check stops on a non-trading day (11-21/22: an email each day
   built from Friday's cached closes) and re-alerts a deeper breach only after a 3% move
   (11-20: 12 emails, one per run, on a falling day).
2. The earnings calendar names a missing E*TRADE session instead of a TypeError.
3. Pattern discovery's Phase 1 logs progress and slow symbols (11-21: timed out with no line).
4. The E*TRADE TOTP login logs what page it saw, without any field value.
"""
import datetime
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "utils"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
import tests  # noqa: F401  hermetic harness
import intraday_monitor as monitor
import pattern_discovery as pd
from aether import earnings_calendar as ec


def _run_monitor(positions, prices, state, trading=True):
    saved, sent = {}, []
    with mock.patch.object(monitor, "_trading_day", return_value=trading), \
         mock.patch.object(monitor, "load_state", return_value=state), \
         mock.patch.object(monitor, "save_state", side_effect=saved.update), \
         mock.patch.object(monitor, "get_monitored_positions", return_value=positions) as pos, \
         mock.patch.object(monitor, "get_live_prices", return_value=prices), \
         mock.patch.object(monitor, "breach_note", return_value="note"), \
         mock.patch("notify.send_email", side_effect=lambda *a, **k: sent.append(a)):
        monitor.monitor()
    return saved, sent, pos


class MonitorTest(unittest.TestCase):
    def test_no_check_on_a_non_trading_day(self):
        saved, sent, pos = _run_monitor([{"symbol": "AAPL", "stop": 150.0}], {"AAPL": 140.0},
                                        {"last_breached": {}}, trading=False)
        self.assertEqual((saved, sent), ({}, []))
        pos.assert_not_called()                       # nothing read, nothing saved, no email

    def test_trading_day_asks_the_market_calendar(self):
        with mock.patch.object(monitor.market_calendar, "is_session", return_value=False) as s:
            self.assertFalse(monitor._trading_day())
        self.assertEqual(s.call_args.args[0], datetime.date.today())

    def test_a_deeper_breach_re_alerts_only_after_3_percent(self):
        alerted = {"AAPL": {"stop": 150.0, "price": 100.0}}
        state = {"last_breached": alerted, "alerted": alerted}
        pos = [{"symbol": "AAPL", "stop": 150.0}]
        _, sent, _ = _run_monitor(pos, {"AAPL": 98.0}, state)           # 2% deeper: no email
        self.assertEqual(sent, [])
        _, sent, _ = _run_monitor(pos, {"AAPL": 96.9}, state)           # 3.1% deeper: email
        self.assertEqual(len(sent), 1)

    def test_a_new_breach_still_alerts_at_once(self):
        _, sent, _ = _run_monitor([{"symbol": "AAPL", "stop": 150.0}], {"AAPL": 149.0}, {"last_breached": {}})
        self.assertEqual(len(sent), 1)


class EarningsCalendarTest(unittest.TestCase):
    def test_no_saved_session_is_named(self):
        client = mock.Mock()
        client.store.tokens.load.return_value = None
        # what PROD's raw_market() did with no token (11-21 17:12)
        client.market.raw_market.side_effect = TypeError("'NoneType' object is not subscriptable")
        with mock.patch.object(ec.etrade, "ETradeClient", return_value=client), \
             self.assertLogs("aether.earnings_calendar", level="WARNING") as logs:
            self.assertEqual(ec.etrade_next(["SA"]), {})
        self.assertIn("no E*TRADE session", logs.output[0])
        self.assertNotIn("TypeError", logs.output[0])
        client.market.raw_market.assert_not_called()
        self.assertEqual(client.store.tokens.load.call_args.args[0], "production")


class PatternDiscoveryProgressTest(unittest.TestCase):
    def test_phase_1_logs_progress_and_slow_symbols(self):
        with mock.patch.object(pd.powergauge, "_build_cache_index"), \
             mock.patch.object(pd, "_get_cached_dates", return_value=[]), \
             mock.patch.object(pd, "PROGRESS_EVERY", 2), \
             mock.patch.object(pd, "SLOW_SYMBOL_S", -1.0), \
             self.assertLogs("aether.pattern_discovery", level="INFO") as logs:
            self.assertEqual(pd.reconstruct_scores("2026-10-27", ["AAA", "BBB", "CCC"]), [])
        text = "\n".join(logs.output)
        self.assertIn("Phase 1: 2/3 symbols, 0 scored", text)
        self.assertIn("Phase 1: 3/3 symbols, 0 scored", text)
        for s in ("AAA", "BBB", "CCC"):
            self.assertIn(f"Phase 1: {s} took", text)

    def test_no_slow_warning_for_a_fast_symbol(self):
        with mock.patch.object(pd.powergauge, "_build_cache_index"), \
             mock.patch.object(pd, "_get_cached_dates", return_value=[]), \
             self.assertLogs("aether.pattern_discovery", level="INFO") as logs:
            pd.reconstruct_scores("2026-10-27", ["AAA"])
        self.assertNotIn("took", "\n".join(logs.output))


class TotpPageStateTest(unittest.TestCase):
    """_page_state is local to _login_headless; read its source to pin what it may log."""

    def test_page_state_logs_no_field_values(self):
        src = open(os.path.join(os.path.dirname(__file__), "..", "aether", "etrade", "__init__.py"),
                   encoding="utf-8").read()
        body = src[src.index("    def _page_state(page)"):src.index("    def _fill_verified(")]
        self.assertNotIn("input_value", body)
        self.assertNotIn(".fill(", body)
        self.assertNotIn("inner_text", body)
        self.assertNotIn("content()", body)
        self.assertIn("is_visible()", body)
        self.assertEqual(src.count('"Page: %s", _page_state(page))'), 1)    # field-not-shown branch
        self.assertIn('No submit button within 8s. Page: %s", _page_state(page)', src)


if __name__ == "__main__":
    unittest.main()
