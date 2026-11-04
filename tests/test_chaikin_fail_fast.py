"""Chaikin outage fail-fast (PROD 2026-10-27 18:00: 550+ breaker warnings and two 30-min timeouts).

While the re-auth breaker is open, ensure_valid_session raises before the JWT exchange and warns
once per breaker window; the batch loops stop on a session error but keep going past one
symbol's network error.
"""
import datetime
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

import requests

import powergauge
import run_history


def _invalid_probe():
    return mock.patch("powergauge._probe_session", return_value="invalid")


class TestBreakerFailsFast(unittest.TestCase):
    def test_open_breaker_raises_before_the_jwt_exchange_and_warns_once(self):
        session = {"jsessionid": "old", "jwttoken": "expired_jwt"}
        until = time.monotonic() + 600
        with mock.patch("powergauge._session_valid_until", 0.0), \
             mock.patch("powergauge._auth_circuit_breaker_until", until), \
             mock.patch("powergauge._breaker_warned_until", 0.0, create=True), \
             mock.patch("powergauge._load_session_from_file", return_value=session), \
             _invalid_probe(), \
             mock.patch("powergauge._jwt_to_session_id") as jwt, \
             mock.patch("powergauge._chaikin_renewer") as renewer, \
             mock.patch.object(powergauge._pg_log, "warning") as warn:
            for _ in range(5):
                with self.assertRaises(powergauge.ChaikinSessionUnavailable):
                    powergauge.ensure_valid_session()
            jwt.assert_not_called()
            renewer.ensure.assert_not_called()
            breaker_warnings = [c for c in warn.call_args_list if "Circuit Breaker" in str(c)]
            self.assertEqual(len(breaker_warnings), 1)

    def test_failed_reauth_is_a_session_error_and_opens_the_breaker(self):
        session = {"jsessionid": "old"}
        with mock.patch("powergauge._session_valid_until", 0.0),              mock.patch("powergauge._auth_circuit_breaker_until", 0.0),              mock.patch("powergauge._load_session_from_file", return_value=session),              _invalid_probe(),              mock.patch("powergauge._chaikin_renewer") as renewer,              mock.patch("powergauge.send_email"):
            renewer.ensure.side_effect = EnvironmentError("Chaikin browser login failed: Timeout")
            with self.assertRaises(powergauge.ChaikinSessionUnavailable):
                powergauge.ensure_valid_session()
            self.assertGreater(powergauge._auth_circuit_breaker_until, time.monotonic())

    def test_closed_breaker_still_tries_the_jwt_exchange(self):
        session = {"jsessionid": "old", "jwttoken": "jwt"}
        with mock.patch("powergauge._session_valid_until", 0.0), \
             mock.patch("powergauge._auth_circuit_breaker_until", 0.0), \
             mock.patch("powergauge._load_session_from_file", return_value=session), \
             _invalid_probe(), \
             mock.patch("powergauge._jwt_to_session_id", return_value="new") as jwt, \
             mock.patch("powergauge._validate_session", return_value=True), \
             mock.patch("powergauge._save_session_to_file"):
            out = powergauge.ensure_valid_session()
        jwt.assert_called_once()
        self.assertEqual(out["jsessionid"], "new")


class TestRunHistoryStops(unittest.TestCase):
    def _run(self, side_effect):
        day = datetime.date(2026, 10, 27)
        calls = []

        def fetch(sym, d, prefer):
            calls.append((sym, d))
            return side_effect(sym)

        with mock.patch.object(sys, "argv", ["run_history.py", "2"]), \
             mock.patch("run_history.load_symbols", return_value=["A", "B", "C", "D"]), \
             mock.patch("run_history.trading_days", return_value=[day, day - datetime.timedelta(days=1)]), \
             mock.patch("run_history.days_missing", side_effect=lambda syms, d: list(syms)), \
             mock.patch("run_history.fetchable", return_value=True), \
             mock.patch("run_history.powergauge.login", return_value={"jsessionid": "x"}), \
             mock.patch("run_history.powergauge.get_symbol_data", side_effect=fetch), \
             mock.patch.dict("os.environ", {"CHAIKIN_WORKERS": "1"}):
            try:
                run_history.main()
                return calls, None
            except EnvironmentError as e:
                return calls, e

    def test_session_error_stops_the_backfill(self):
        def boom(sym):
            raise powergauge.ChaikinSessionUnavailable("Chaikin automated re-auth suspended (Circuit Breaker).")
        calls, err = self._run(boom)
        self.assertIsNotNone(err)
        self.assertEqual(len({d for _, d in calls}), 1)   # never starts the second day
        self.assertLess(len(calls), 4 * 2)

    def test_other_errors_are_per_symbol(self):
        # requests' exceptions and a locked cache file are OSErrors too (#226 review).
        ok = mock.MagicMock(price=10.0)
        for exc in (requests.ConnectionError("Max retries exceeded"), PermissionError("file in use")):
            def flaky(sym, exc=exc):
                if sym == "B":
                    raise exc
                return ok
            with self.subTest(exc=type(exc).__name__):
                calls, err = self._run(flaky)
                self.assertIsNone(err)
                self.assertEqual(len(calls), 8)            # every symbol, both days


class TestScreenKeepsGoingPastNetworkErrors(unittest.TestCase):
    def test_check_from_file_skips_a_network_error_symbol(self):
        with tempfile.TemporaryDirectory() as td:
            syms = os.path.join(td, "symbols_to_check.txt")
            with open(syms, "w") as f:
                f.write("1 AAA\n2 BBB\n")
            good = powergauge.PowerGauge("AAA", datetime.date(2026, 10, 27))

            def fetch(sym, d, prefer):
                if sym == "BBB":
                    raise PermissionError("cache file in use")   # an OSError, not a session error
                return good
            real_join = os.path.join

            def join(*a):
                if a[-1] == "symbols_to_check.txt":
                    return syms
                if str(a[-1]).startswith("symbols_to_check_"):
                    return real_join(td, a[-1])
                return real_join(*a)
            with mock.patch("powergauge.login", return_value={"jsessionid": "x"}), \
                 mock.patch("powergauge._build_cache_index"), \
                 mock.patch("powergauge.get_symbol_data", side_effect=fetch), \
                 mock.patch("powergauge.os.path.join", side_effect=join), \
                 mock.patch("powergauge.compute_pg_row", create=True, return_value=None):
                try:
                    powergauge.check_from_file(prefer_cache=False, date=datetime.date(2026, 10, 27))
                except OSError:
                    self.fail("one symbol's OSError aborted the whole screen")


if __name__ == "__main__":
    unittest.main()
