"""B1 characterization tests for aether.scenario.prices.

The package price chain is built additively (root ``ai_portfolio_game`` untouched
until R1). These tests pin it against the SAME contract ``tests/test_game_pricing.py``
pins ``get_live_prices`` to, so the eventual R1 swap is provably behaviour-identical:

* Differential tests run ``game.get_live_prices`` and ``make_price_source().get_prices``
  under one set of patches and assert they return the same dict and make the same
  Google call — the strongest possible characterization anchor for the swap.
* Gate tests exercise each branch of the fallback chain (weekend, after-hours+fresh
  workbook, weekday E*TRADE, auth-fail, exception) directly on the package.
* Adapter tests prove every leaf resolves its collaborator off the ``ai_portfolio_game``
  module at CALL TIME via ``_pkg()`` (so ``mock.patch.object`` keeps intercepting).

E*TRADE + Google + the workbook are mocked; no network, no disk.
"""
import datetime
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ai_portfolio_game as game  # noqa: E402
from aether.scenario import prices  # noqa: E402
from aether.scenario.prices import (  # noqa: E402
    ChainedPriceSource,
    EtradeSource,
    GoogleSource,
    JsonCacheSource,
    PriceSource,
    WorkbookSource,
    _missing,
    make_price_source,
)


# --- Frozen clocks (subclass datetime.date so the module's datetime.date swap works) --

class WednesdayDate(datetime.date):
    @classmethod
    def today(cls):
        return cls(2026, 8, 5)  # Wednesday (weekday 2) — matches test_game_pricing


class SaturdayDate(datetime.date):
    @classmethod
    def today(cls):
        return cls(2026, 8, 8)  # Saturday (weekday 5)


class WednesdayFreshWorkbook(datetime.date):
    """Weekday whose fromtimestamp() always equals today() -> workbook reads FRESH,
    independent of the machine's timezone."""
    @classmethod
    def today(cls):
        return cls(2026, 8, 5)

    @classmethod
    def fromtimestamp(cls, ts):
        return cls(2026, 8, 5)


class WednesdayStaleWorkbook(datetime.date):
    """Weekday whose fromtimestamp() is an earlier date -> workbook reads STALE."""
    @classmethod
    def today(cls):
        return cls(2026, 8, 5)

    @classmethod
    def fromtimestamp(cls, ts):
        return cls(2026, 8, 1)


# ---------------------------------------------------------------------------
# Port / helper
# ---------------------------------------------------------------------------

class TestPortAndHelper(unittest.TestCase):
    def test_price_source_is_abstract(self):
        with self.assertRaises(TypeError):
            PriceSource()

    def test_missing_flags_absent_zero_negative(self):
        symbols = ["AAA", "BBB", "CCC", "DDD"]
        quotes = {"AAA": 10.0, "BBB": 0, "CCC": -5}  # DDD absent
        self.assertEqual(_missing(symbols, quotes), ["BBB", "CCC", "DDD"])

    def test_missing_empty_when_all_priced(self):
        self.assertEqual(_missing(["AAA"], {"AAA": 1.0}), [])


# ---------------------------------------------------------------------------
# Differential: package chain == game.get_live_prices under identical patches
# ---------------------------------------------------------------------------

class TestDifferentialAgainstGetLivePrices(unittest.TestCase):
    """The package must return exactly what get_live_prices returns, call-for-call."""

    def test_partial_fill_matches(self):
        def run(fn):
            with mock.patch("ai_portfolio_game.datetime.date", WednesdayDate), \
                 mock.patch.object(game, "is_market_hours", return_value=True), \
                 mock.patch.object(game.etrade, "get_tokens", return_value=["tok"]), \
                 mock.patch.object(game.etrade, "fetch_quotes",
                                   return_value={"AAA": 10.0}), \
                 mock.patch.object(game, "get_google_prices_fallback",
                                   return_value={"BBB": 20.0}) as goog:
                out = fn(["AAA", "BBB"])
            return out, goog

        game_out, game_goog = run(game.get_live_prices)
        pkg_out, pkg_goog = run(make_price_source().get_prices)

        self.assertEqual(game_out, {"AAA": 10.0, "BBB": 20.0})
        self.assertEqual(pkg_out, game_out)
        game_goog.assert_called_once_with(["BBB"])
        pkg_goog.assert_called_once_with(["BBB"])

    def test_no_missing_matches(self):
        def run(fn):
            with mock.patch("ai_portfolio_game.datetime.date", WednesdayDate), \
                 mock.patch.object(game, "is_market_hours", return_value=True), \
                 mock.patch.object(game.etrade, "get_tokens", return_value=["tok"]), \
                 mock.patch.object(game.etrade, "fetch_quotes",
                                   return_value={"AAA": 10.0, "BBB": 20.0}), \
                 mock.patch.object(game, "get_google_prices_fallback") as goog:
                out = fn(["AAA", "BBB"])
            return out, goog

        game_out, game_goog = run(game.get_live_prices)
        pkg_out, pkg_goog = run(make_price_source().get_prices)

        self.assertEqual(pkg_out, game_out)
        self.assertEqual(pkg_out, {"AAA": 10.0, "BBB": 20.0})
        game_goog.assert_not_called()
        pkg_goog.assert_not_called()


# ---------------------------------------------------------------------------
# ChainedPriceSource gates (tested directly on the package with injected fakes)
# ---------------------------------------------------------------------------

class _FakeSource(PriceSource):
    """Records the symbols it was asked for and returns a canned dict."""
    def __init__(self, ret):
        self.ret = dict(ret)
        self.calls = []

    def get_prices(self, symbols):
        self.calls.append(list(symbols))
        return dict(self.ret)


class TestChainGates(unittest.TestCase):
    def _chain(self, json_ret, etrade_ret, google_ret):
        self.json = _FakeSource(json_ret)
        self.etrade = _FakeSource(etrade_ret)
        self.google = _FakeSource(google_ret)
        return ChainedPriceSource(self.json, self.etrade, self.google)

    def test_weekend_uses_json_cache_and_gap_fills(self):
        chain = self._chain({"AAA": 5.0}, {"NOPE": 1.0}, {"BBB": 6.0})
        with mock.patch("ai_portfolio_game.datetime.date", SaturdayDate):
            out = chain.get_prices(["AAA", "BBB"])
        self.assertEqual(out, {"AAA": 5.0, "BBB": 6.0})
        self.assertEqual(self.json.calls, [["AAA", "BBB"]])
        self.assertEqual(self.etrade.calls, [])           # E*TRADE never touched on weekend
        self.assertEqual(self.google.calls, [["BBB"]])    # only the gap scraped

    def test_afterhours_fresh_workbook_uses_json_cache(self):
        chain = self._chain({"AAA": 7.0, "BBB": 8.0}, {"NOPE": 1.0}, {})
        with mock.patch("ai_portfolio_game.datetime.date", WednesdayFreshWorkbook), \
             mock.patch.object(game, "is_market_hours", return_value=False), \
             mock.patch.object(game.os.path, "exists", return_value=True), \
             mock.patch.object(game.os.path, "getmtime", return_value=1.0):
            out = chain.get_prices(["AAA", "BBB"])
        self.assertEqual(out, {"AAA": 7.0, "BBB": 8.0})
        self.assertEqual(self.json.calls, [["AAA", "BBB"]])
        self.assertEqual(self.etrade.calls, [])           # fresh workbook -> skip E*TRADE
        self.assertEqual(self.google.calls, [])           # nothing missing

    def test_afterhours_stale_workbook_falls_through_to_etrade(self):
        chain = self._chain({"CACHE": 1.0}, {"AAA": 9.0, "BBB": 9.5}, {})
        with mock.patch("ai_portfolio_game.datetime.date", WednesdayStaleWorkbook), \
             mock.patch.object(game, "is_market_hours", return_value=False), \
             mock.patch.object(game.os.path, "exists", return_value=True), \
             mock.patch.object(game.os.path, "getmtime", return_value=1.0):
            out = chain.get_prices(["AAA", "BBB"])
        self.assertEqual(out, {"AAA": 9.0, "BBB": 9.5})
        self.assertEqual(self.json.calls, [])             # stale -> cache NOT used
        self.assertEqual(self.etrade.calls, [["AAA", "BBB"]])
        self.assertEqual(self.google.calls, [])

    def test_weekday_market_etrade_then_gap_fill(self):
        chain = self._chain({}, {"AAA": 10.0}, {"BBB": 20.0})
        with mock.patch("ai_portfolio_game.datetime.date", WednesdayDate), \
             mock.patch.object(game, "is_market_hours", return_value=True):
            out = chain.get_prices(["AAA", "BBB"])
        self.assertEqual(out, {"AAA": 10.0, "BBB": 20.0})
        self.assertEqual(self.etrade.calls, [["AAA", "BBB"]])
        self.assertEqual(self.google.calls, [["BBB"]])    # only the gap

    def test_etrade_empty_auth_fail_gap_fills_whole_list(self):
        # EtradeSource returns {} on auth fail -> every symbol is a gap -> google(all).
        chain = self._chain({}, {}, {"AAA": 1.0, "BBB": 2.0})
        with mock.patch("ai_portfolio_game.datetime.date", WednesdayDate), \
             mock.patch.object(game, "is_market_hours", return_value=True):
            out = chain.get_prices(["AAA", "BBB"])
        self.assertEqual(out, {"AAA": 1.0, "BBB": 2.0})
        self.assertEqual(self.google.calls, [["AAA", "BBB"]])

    def test_exception_anywhere_falls_back_to_whole_list_google(self):
        chain = self._chain({}, {}, {"AAA": 1.0, "BBB": 2.0})
        with mock.patch("ai_portfolio_game.datetime.date") as dt:
            dt.today.side_effect = RuntimeError("clock blew up")
            out = chain.get_prices(["AAA", "BBB"])
        self.assertEqual(out, {"AAA": 1.0, "BBB": 2.0})
        self.assertEqual(self.google.calls, [["AAA", "BBB"]])
        self.assertEqual(self.etrade.calls, [])


# ---------------------------------------------------------------------------
# Leaf adapters resolve collaborators via _pkg() at call time
# ---------------------------------------------------------------------------

class TestLeafAdapters(unittest.TestCase):
    def test_json_cache_delegates(self):
        with mock.patch.object(game, "get_json_prices_fallback",
                               return_value={"AAA": 3.0}) as f:
            out = JsonCacheSource().get_prices(["AAA"])
        self.assertEqual(out, {"AAA": 3.0})
        f.assert_called_once_with(["AAA"])

    def test_google_delegates(self):
        with mock.patch.object(game, "get_google_prices_fallback",
                               return_value={"AAA": 4.0}) as f:
            out = GoogleSource().get_prices(["AAA"])
        self.assertEqual(out, {"AAA": 4.0})
        f.assert_called_once_with(["AAA"])

    def test_etrade_reproduces_client_path(self):
        with mock.patch.object(game.etrade, "get_tokens", return_value=["tok"]), \
             mock.patch.object(game.etrade, "fetch_quotes",
                               return_value={"AAA": 11.0}) as fq:
            out = EtradeSource().get_prices(["AAA"])
        self.assertEqual(out, {"AAA": 11.0})
        fq.assert_called_once()

    def test_etrade_returns_empty_on_auth_fail(self):
        with mock.patch.object(game.etrade, "get_tokens", return_value=None), \
             mock.patch.object(game.etrade, "fetch_quotes") as fq:
            out = EtradeSource().get_prices(["AAA"])
        self.assertEqual(out, {})
        fq.assert_not_called()  # no quotes attempted without tokens


# ---------------------------------------------------------------------------
# WorkbookSource — magic-index-preserving reader (patched openpyxl)
# ---------------------------------------------------------------------------

class _FakeWorksheet:
    def __init__(self, rows):
        self._rows = rows

    def iter_rows(self, min_row=1, values_only=True):
        # 1-indexed rows; skip everything before min_row.
        for r in self._rows[min_row - 1:]:
            yield r


class _FakeWorkbook:
    def __init__(self, sheets):
        self._sheets = sheets
        self.sheetnames = list(sheets.keys())
        self.closed = False

    def __getitem__(self, name):
        return self._sheets[name]

    def close(self):
        self.closed = True


class TestWorkbookSource(unittest.TestCase):
    def test_short_long_uses_row1_symbol_row4_price(self):
        # Rows 1-2 are header padding (reader starts at min_row=3).
        rows = [
            ("hdr",) * 6,
            ("hdr",) * 6,
            (None, "AAA", "x", "y", 12.5, "z"),   # row[1]=AAA, row[4]=12.5
            (None, "BBB", "x", "y", 0, "z"),      # zero price -> skipped
            (None, "CCC", "x", "y", 30.0, "z"),
        ]
        wb = _FakeWorkbook({"Short_Long": _FakeWorksheet(rows)})
        with mock.patch.object(game.openpyxl, "load_workbook", return_value=wb):
            out = WorkbookSource().get_prices(["AAA", "CCC", "ZZZ"])
        self.assertEqual(out, {"AAA": 12.5, "CCC": 30.0})   # BBB(zero)/ZZZ(absent) out
        self.assertTrue(wb.closed)

    def test_research_uses_row3_symbol_row10_price(self):
        # No Short_Long sheet -> falls to Research; reader starts at min_row=2.
        rows = [
            ("hdr",) * 11,
            (None, None, None, "AAA", "ind", None, "pgr", None, 99.0, "stop", 42.0),
            (None, None, None, "BBB", "ind", None, "pgr", None, 88.0, "stop", 50.0),
        ]
        wb = _FakeWorkbook({"Research": _FakeWorksheet(rows)})
        with mock.patch.object(game.openpyxl, "load_workbook", return_value=wb):
            out = WorkbookSource().get_prices(["AAA", "BBB"])
        # row[10] is the BUY/report prev-close column (42.0 / 50.0) — NOT row[8] (99/88).
        self.assertEqual(out, {"AAA": 42.0, "BBB": 50.0})

    def test_load_failure_returns_empty(self):
        with mock.patch.object(game.openpyxl, "load_workbook",
                               side_effect=Exception("locked")):
            out = WorkbookSource().get_prices(["AAA"])
        self.assertEqual(out, {})


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

class TestFactory(unittest.TestCase):
    def test_make_price_source_wires_default_chain(self):
        src = make_price_source()
        self.assertIsInstance(src, ChainedPriceSource)
        self.assertIsInstance(src.json_cache, JsonCacheSource)
        self.assertIsInstance(src.etrade, EtradeSource)
        self.assertIsInstance(src.google, GoogleSource)

    def test_env_propagates_to_etrade_adapter(self):
        src = make_price_source(env="sandbox")
        self.assertEqual(src.env, "sandbox")
        self.assertEqual(src.etrade.env, "sandbox")

    def test_module_exports_public_surface(self):
        for name in ("PriceSource", "JsonCacheSource", "GoogleSource", "EtradeSource",
                     "WorkbookSource", "ChainedPriceSource", "make_price_source"):
            self.assertTrue(hasattr(prices, name))


if __name__ == "__main__":
    unittest.main()
