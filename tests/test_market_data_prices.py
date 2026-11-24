"""Market-data service: the live-price chain (R&D #78 S5, plan P4). Fakes only; no network."""
import datetime
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import ai_portfolio_game as game
import aether.bootstrap as boot
from aether import etrade
from aether.services.market_data.adapters.prices import EtradeQuotes, GooglePrices, OhlcvCloses
from aether.services.market_data.api import PriceService, cached_close_is_current, missing

WED, SAT = datetime.date(2026, 11, 18), datetime.date(2026, 11, 21)


class _Source:
    def __init__(self, prices):
        self.prices, self.calls = dict(prices), []

    def quotes(self, symbols, today=None):
        self.calls.append(list(symbols))
        return {s: self.prices[s] for s in symbols if s in self.prices}


class _Broker:
    def __init__(self, prices, logged_in=True, error=None):
        self.prices, self.logged_in, self.error, self.calls = prices, logged_in, error, 0

    def session(self):
        if self.error:
            raise self.error
        return ("client", "tok") if self.logged_in else None

    def quotes(self, session, symbols):
        self.calls += 1
        return {s: self.prices[s] for s in symbols if s in self.prices}


class TestRules(unittest.TestCase):
    def test_missing(self):
        self.assertEqual(missing(["A", "B", "C", "D"], {"A": 1.0, "B": 0, "C": -1}), ["B", "C", "D"])

    def test_cached_close_is_current(self):
        self.assertTrue(cached_close_is_current("2026-11-18", WED))
        self.assertTrue(cached_close_is_current("2026-11-14", WED))   # 4 days old
        self.assertFalse(cached_close_is_current("2026-11-13", WED))  # 5 days old
        self.assertTrue(cached_close_is_current("2026-01-02", SAT))   # any close on a weekend


class TestPriceChain(unittest.TestCase):
    def setUp(self):
        self.log = mock.Mock()
        self.cache = _Source({"AAA": 1.0})
        self.google = _Source({"AAA": 9.0, "BBB": 2.0, "CCC": 3.0})

    def _svc(self, broker):
        return PriceService(broker, self.google, self.cache, log=self.log)

    def _run(self, broker, today=WED, hours=True, fresh=False):
        asked = []
        out = self._svc(broker).live_prices(
            ["AAA", "BBB"], today=today,
            market_hours=lambda: asked.append("hours") or hours,
            workbook_fresh=lambda: asked.append("fresh") or fresh)
        return out, asked

    def test_weekend_uses_the_cache_then_google_and_asks_nothing_else(self):
        broker = _Broker({"AAA": 5.0})
        out, asked = self._run(broker, today=SAT)
        self.assertEqual(out, {"AAA": 1.0, "BBB": 2.0})
        self.assertEqual((asked, broker.calls, self.google.calls), ([], 0, [["BBB"]]))

    def test_after_hours_with_todays_workbook_uses_the_cache(self):
        broker = _Broker({"AAA": 5.0})
        out, asked = self._run(broker, hours=False, fresh=True)
        self.assertEqual((out, asked, broker.calls), ({"AAA": 1.0, "BBB": 2.0}, ["hours", "fresh"], 0))

    def test_after_hours_with_a_stale_workbook_asks_the_broker(self):
        out, asked = self._run(_Broker({"AAA": 5.0, "BBB": 6.0}), hours=False, fresh=False)
        self.assertEqual((out, asked), ({"AAA": 5.0, "BBB": 6.0}, ["hours", "fresh"]))
        self.assertIn("STALE", self.log.console.call_args_list[0][0][0])

    def test_market_hours_skip_the_workbook_check(self):
        _, asked = self._run(_Broker({"AAA": 5.0, "BBB": 6.0}))
        self.assertEqual(asked, ["hours"])

    def test_google_fills_only_the_broker_gaps(self):
        out, _ = self._run(_Broker({"AAA": 5.0}))
        self.assertEqual(out, {"AAA": 5.0, "BBB": 2.0})
        self.assertEqual(self.google.calls, [["BBB"]])

    def test_a_symbol_no_source_prices_is_logged(self):
        self.google.prices = {}
        out, _ = self._run(_Broker({"AAA": 5.0}))
        self.assertEqual(out, {"AAA": 5.0})
        self.assertIn("SYMBOLOGY ERROR", self.log.console.call_args[0][0])

    def test_no_login_means_google_for_everything(self):
        out, _ = self._run(_Broker({}, logged_in=False))
        self.assertEqual((out, self.google.calls), ({"AAA": 9.0, "BBB": 2.0}, [["AAA", "BBB"]]))
        self.assertIn("authentication failed", self.log.warning.call_args[0][0])

    def test_an_error_means_google_for_everything(self):
        out, _ = self._run(_Broker({}, error=RuntimeError("down")))
        self.assertEqual(out, {"AAA": 9.0, "BBB": 2.0})
        self.assertIn("E*TRADE connection failed: down", self.log.warning.call_args[0][0])


class TestSources(unittest.TestCase):
    def test_ohlcv_closes(self):
        with tempfile.TemporaryDirectory() as d:
            for sym, day in (("AAA", "2026-11-18"), ("OLD", "2026-11-10")):
                Path(d, f"{sym}_daily.json").write_text(json.dumps(
                    {"Time Series (Daily)": {"2026-11-01": {"4. close": "1"}, day: {"4. close": "12.5"}}}))
            Path(d, "BAD_daily.json").write_text("{broken")
            log = mock.Mock()
            src = OhlcvCloses(d, log=log)
            self.assertEqual(src.quotes(["AAA", "OLD", "NONE"], WED), {"AAA": 12.5})
            self.assertEqual(src.quotes(["AAA", "BAD"], WED), {})  # one bad file -> {} (as before)
            self.assertIn("Failed to load local JSON prices", log.console.call_args[0][0])

    def test_ohlcv_closes_without_today_uses_the_local_date(self):
        # #320 review: without ``today`` the source returned {} and logged a misleading error.
        with tempfile.TemporaryDirectory() as d:
            Path(d, "AAA_daily.json").write_text(json.dumps(
                {"Time Series (Daily)": {str(datetime.date.today()): {"4. close": "7.5"}}}))
            log = mock.Mock()
            self.assertEqual(OhlcvCloses(d, log=log).quotes(["AAA"]), {"AAA": 7.5})
        log.console.assert_not_called()

    def test_google_parses_the_price_and_tries_each_exchange(self):
        page = mock.Mock(status_code=200, text='<div jsname="Pdsbrc" class="x"> <span>$1,234.50</span></div>')
        miss = mock.Mock(status_code=404, text="")
        with mock.patch("requests.get", side_effect=[miss, page]) as get:
            self.assertEqual(GooglePrices(log=mock.Mock()).price("AAA"), 1234.5)
        self.assertEqual([c.args[0] for c in get.call_args_list],
                         ["https://www.google.com/finance/quote/AAA:NASDAQ", "https://www.google.com/finance/quote/AAA:NYSE"])

    def test_etrade_session_and_quotes(self):
        client = mock.Mock()
        client.auth.get_tokens.return_value = None
        with mock.patch.object(etrade, "ETradeClient", return_value=client):
            self.assertIsNone(EtradeQuotes().session())
            client.auth.get_tokens.return_value = {"t": 1}
            client.market.quotes.return_value = {"AAA": 5.0}
            src = EtradeQuotes()
            self.assertEqual(src.quotes(src.session(), ["AAA"]), {"AAA": 5.0})


class TestGameUsesTheChain(unittest.TestCase):
    def test_wiring(self):
        with mock.patch.object(game, "market_prices", wraps=game.market_prices) as mp:
            game.get_json_prices_fallback([])
        mp.assert_called_once_with(cache_dir=game.SYMBOL_FULL_DIR, log=game._log)
        self.assertIsInstance(boot.market_prices(cache_dir="x").cache, OhlcvCloses)

    def test_game_passes_today_and_its_checks(self):
        seen = {}

        def fake(self, symbols, *, today, market_hours, workbook_fresh):
            seen.update(today=today, hours=market_hours(), fresh=workbook_fresh())
            return {"AAA": 1.0}

        with mock.patch.object(PriceService, "live_prices", fake), \
                mock.patch.object(game, "is_market_hours", return_value=False), \
                mock.patch.object(game, "_workbook_synced_today", return_value=True):
            self.assertEqual(game.get_live_prices(["AAA"]), {"AAA": 1.0})
        self.assertEqual(seen, {"today": datetime.date.today(), "hours": False, "fresh": True})


if __name__ == "__main__":
    unittest.main()
