"""Market-data service: session rules, the live clock, and one clock question per run (R&D #78 S5)."""
import datetime
import unittest
from unittest import mock

import pytz

import ai_portfolio_game as game
import aether.bootstrap as boot
from aether import etrade
from aether.core import FixedClock
from aether.services.market_data.adapters.etrade_clock import EtradeMarketClock
from aether.services.market_data.api import SessionService, in_trading_window, regular_hours, run_scope

ET = pytz.timezone("America/New_York")
LA = pytz.timezone("America/Los_Angeles")


def _et(y, m, d, hh, mm):
    return ET.localize(datetime.datetime(y, m, d, hh, mm))


def _la(y, m, d, hh, mm):
    return LA.localize(datetime.datetime(y, m, d, hh, mm))


class _Clock:
    def __init__(self, answer):
        self.answer, self.calls = answer, 0

    def is_open_now(self):
        self.calls += 1
        return self.answer


class TestRegularHours(unittest.TestCase):
    def test_messages(self):
        self.assertEqual(regular_hours(_et(2026, 11, 20, 10, 0)), (True, "Market is Open."))
        self.assertEqual(regular_hours(_et(2026, 11, 21, 10, 0)), (False, "Market is closed (Weekend)."))
        self.assertEqual(regular_hours(_et(2026, 11, 26, 10, 0)), (False, "Market is closed (NYSE holiday 2026-11-26)."))
        self.assertEqual(regular_hours(_et(2026, 11, 20, 9, 29)), (False, "Market is Closed. (Current EST: 09:29)"))

    def test_edges_and_other_time_zones(self):
        self.assertTrue(regular_hours(_et(2026, 11, 20, 9, 30))[0])
        self.assertTrue(regular_hours(_et(2026, 11, 20, 16, 0))[0])
        self.assertFalse(regular_hours(_et(2026, 11, 20, 16, 1))[0])
        self.assertTrue(regular_hours(_la(2026, 11, 20, 7, 0))[0])  # 10:00 ET


class TestTradingWindow(unittest.TestCase):
    def test_window(self):
        self.assertFalse(in_trading_window(_la(2026, 11, 20, 6, 29)))
        self.assertTrue(in_trading_window(_la(2026, 11, 20, 6, 30)))
        self.assertTrue(in_trading_window(_la(2026, 11, 20, 13, 15)))
        self.assertFalse(in_trading_window(_la(2026, 11, 20, 13, 16)))
        self.assertFalse(in_trading_window(_la(2026, 11, 21, 9, 0)))   # Saturday
        self.assertFalse(in_trading_window(_la(2026, 11, 26, 9, 0)))   # Thanksgiving


class TestSessionService(unittest.TestCase):
    def setUp(self):
        self.log = mock.Mock()

    def _svc(self, answer, at=None):
        clock = _Clock(answer)
        return SessionService(FixedClock(at or _la(2026, 11, 20, 9, 0)), clock, log=self.log), clock

    def test_live_clock_decides_inside_the_window(self):
        for answer, expected in ((True, True), (False, False), (None, True)):
            with self.subTest(answer=answer):
                svc, _ = self._svc(answer)
                self.assertIs(svc.market_hours(), expected)

    def test_outside_the_window_the_clock_is_not_asked(self):
        svc, clock = self._svc(True, at=_la(2026, 11, 20, 14, 0))
        self.assertFalse(svc.market_hours())
        self.assertEqual(clock.calls, 0)

    def test_an_error_means_skip(self):
        svc, _ = self._svc(True)
        with mock.patch("aether.services.market_data.app.in_trading_window", side_effect=ValueError("tz")):
            self.assertFalse(svc.market_hours())
        self.assertIn("defaulting to False", self.log.warning.call_args[0][0])

    def test_one_clock_question_per_run(self):
        svc, clock = self._svc(False)
        with run_scope():
            self.assertEqual([svc.market_hours() for _ in range(5)], [False] * 5)
        self.assertEqual(clock.calls, 1)
        svc.market_hours()
        svc.market_hours()
        self.assertEqual(clock.calls, 3)  # outside a run, each call asks again

    def test_each_run_asks_afresh(self):
        svc, clock = self._svc(True)
        for _ in range(2):
            with run_scope():
                svc.market_hours()
                svc.market_hours()
        self.assertEqual(clock.calls, 2)

    def test_market_open_uses_the_injected_clock(self):
        svc, _ = self._svc(True, at=_et(2026, 11, 26, 10, 0))
        self.assertEqual(svc.market_open(), (False, "Market is closed (NYSE holiday 2026-11-26)."))


class TestEtradeMarketClock(unittest.TestCase):
    def _client(self, tokens=True, is_open=True, error=None):
        c = mock.Mock()
        c.auth.get_tokens.return_value = {"t": 1} if tokens else None
        if error:
            c.auth.get_tokens.side_effect = error
        c.market.is_open_now.return_value = is_open
        return mock.Mock(return_value=c)

    def test_answers(self):
        log = mock.Mock()
        for kwargs, expected in (({}, True), ({"is_open": False}, False), ({"is_open": None}, None),
                                 ({"tokens": False}, None), ({"error": RuntimeError("x")}, None)):
            with self.subTest(**{k: str(v) for k, v in kwargs.items()}), \
                    mock.patch.object(etrade, "ETradeClient", self._client(**kwargs)):
                self.assertIs(EtradeMarketClock(log=log).is_open_now(), expected)
        self.assertIn("CLOSED", log.console.call_args[0][0])
        self.assertIn("Dynamic market clock failed: x", log.warning.call_args[0][0])


class TestGameUsesTheService(unittest.TestCase):
    def test_bootstrap_wires_the_etrade_clock(self):
        self.assertIsInstance(boot.bootstrap().market_data._market_clock, EtradeMarketClock)
        fake = _Clock(True)
        self.assertIs(boot.bootstrap(market_clock=fake).market_data._market_clock, fake)

    def test_a_daily_run_asks_the_live_clock_once(self):
        fixed = _la(2026, 11, 20, 9, 0)

        class _DT(datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)

        dtmod = mock.Mock(wraps=datetime, datetime=_DT, date=datetime.date, timedelta=datetime.timedelta)
        client = mock.Mock()
        client.auth.get_tokens.return_value = {"t": 1}
        client.market.is_open_now.return_value = True
        make = mock.Mock(return_value=client)
        with mock.patch.object(game, "datetime", dtmod), mock.patch.object(etrade, "ETradeClient", make):
            with run_scope():  # what runners.run_daily_management does around a run
                self.assertTrue(all(game.is_market_hours() for _ in range(6)))
            self.assertEqual(make.call_count, 1)
            game.is_market_hours()
            self.assertEqual(make.call_count, 2)

    def test_market_checks_do_not_depend_on_the_portfolio_store(self):
        # #309 review: AETHER_PORTFOLIO_STORE=db (refused until S14) must not break a market question.
        import os
        fixed = _la(2026, 11, 20, 9, 0)

        class _DT(datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)

        dtmod = mock.Mock(wraps=datetime, datetime=_DT, date=datetime.date, timedelta=datetime.timedelta)
        client = mock.Mock()
        client.auth.get_tokens.return_value = {"t": 1}
        client.market.is_open_now.return_value = True
        with mock.patch.dict(os.environ, {"AETHER_PORTFOLIO_STORE": "db"}), \
                mock.patch.object(game, "datetime", dtmod), \
                mock.patch.object(etrade, "ETradeClient", mock.Mock(return_value=client)):
            self.assertTrue(game.is_market_hours())
            self.assertEqual(game.is_market_open(), (True, "Market is Open."))

    def test_a_failing_service_means_not_market_hours(self):
        with mock.patch.object(game, "_market", side_effect=RuntimeError("wiring")), \
                mock.patch.object(game, "_log") as log:
            self.assertFalse(game.is_market_hours())
        self.assertIn("defaulting to False", log.warning.call_args[0][0])

    def test_the_runner_opens_a_run_scope(self):
        from aether.scenario import runners
        seen = []
        with mock.patch.object(runners, "_run_daily_management",
                               side_effect=lambda **kw: seen.append(__import__(
                                   "aether.services.market_data.app", fromlist=["_RUN"])._RUN.get())):
            runners.run_daily_management(force=True)
        self.assertEqual(seen, [{}])  # inside a fresh scope


if __name__ == "__main__":
    unittest.main()
