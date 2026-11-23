"""aether/earnings_calendar.py: next earnings dates as an advisory flag on recommendations.

PROD 2026-11-18/19: SA was bought and recommended on 11-18 and fell ~30% on 11-19; no recommendation
could say when a company reports. Two sources each carry a date for some symbols: E*TRADE quotes
(All.nextEarningDate, "MM/DD/YYYY", sometimes already past) and Chaikin's raw response
(epsData.nextReportDate, "Thu Dec 17", no year). The flag is advisory; nothing is blocked.
"""
import ast
import datetime
import gzip
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tests  # noqa: F401  hermetic harness (redirects the calendar file)
import aether_oracle as oracle
from aether import earnings_calendar as ec

_REPO = Path(__file__).resolve().parent.parent
D = datetime.date


class ParseTest(unittest.TestCase):
    def test_etrade(self):
        self.assertEqual(ec.parse_etrade("11/20/2026"), D(2026, 11, 20))
        for bad in ("", None, "soon", "2026-11-20"):
            self.assertIsNone(ec.parse_etrade(bad))

    def test_chaikin_infers_the_year(self):
        today = D(2026, 11, 19)
        self.assertEqual(ec.parse_chaikin("Thu Dec 17", today), D(2026, 12, 17))
        self.assertEqual(ec.parse_chaikin("Wed Feb 03", today), D(2027, 2, 3))      # more than a month past -> next year
        self.assertEqual(ec.parse_chaikin("Wed Nov 18", today), D(2026, 11, 18))    # just past stays this year
        self.assertEqual(ec.parse_chaikin("Wed Jan 27", D(2026, 12, 20)), D(2027, 1, 27))
        self.assertIsNone(ec.parse_chaikin("", today))


class BuildAndNoteTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.raw = os.path.join(self.td.name, "Symbol_raw")
        p = mock.patch.object(ec.paths, "symbol_raw_dir", return_value=self.raw)
        p.start()
        self.addCleanup(p.stop)

    def _raw(self, sym, day, next_report):
        os.makedirs(os.path.join(self.raw, sym), exist_ok=True)
        with gzip.open(os.path.join(self.raw, sym, f"{sym}_{day}.json.gz"), "wt", encoding="utf-8") as f:
            json.dump({"data": {"epsData": {"nextReportDate": next_report}}}, f)

    def test_the_earliest_future_date_from_either_source(self):
        today = D(2026, 11, 19)
        self._raw("ADSK", "2026-11-19", "Tue Nov 24")    # Chaikin only (E*TRADE was empty for ADSK on 11-19)
        self._raw("BABA", "2026-11-19", "Wed Nov 18")    # Chaikin's date is past; E*TRADE has 11-20
        self._raw("SA", "2026-11-19", None)               # neither (as on 11-19)
        self._raw("NVDA", "2026-11-19", "Tue Nov 24")    # both future: the earlier one wins
        et = {"BABA": D(2026, 11, 20), "AAPL": D(2026, 11, 2), "NVDA": D(2026, 11, 21)}   # AAPL: a past date, not a next one
        cal = ec.build(["adsk", "BABA", "SA", "AAPL", "NVDA"], today, fetch_etrade=lambda syms: et)
        s = cal["symbols"]
        self.assertEqual(s["ADSK"]["next"], "2026-11-24")
        self.assertEqual(s["BABA"]["next"], "2026-11-20")
        self.assertIsNone(s["SA"]["next"])
        self.assertIsNone(s["AAPL"]["next"])
        self.assertEqual(s["NVDA"]["next"], "2026-11-21")
        self.assertEqual(ec.load(), cal)                  # saved (to the harness's temp path)

    def test_note(self):
        today = D(2026, 11, 19)
        cal = {"built": "2026-11-19", "symbols": {
            "A": {"next": "2026-11-19"}, "B": {"next": "2026-11-20"}, "C": {"next": "2026-11-24"},
            "D": {"next": "2026-11-27"}, "E": {"next": None}}}
        self.assertEqual(ec.note("A", today, cal), "earnings today (2026-11-19)")
        self.assertEqual(ec.note("b", today, cal), "earnings tomorrow (2026-11-20)")
        self.assertEqual(ec.note("C", today, cal), "earnings in 5 days (2026-11-24)")
        self.assertEqual(ec.note("D", today, cal), "")                     # 8 days: beyond WARN_DAYS
        self.assertEqual(ec.note("E", today, cal), "")
        self.assertEqual(ec.note("E", today, cal, unknown=True), "earnings date unknown")
        self.assertEqual(ec.note("ZZ", today, cal, unknown=True), "")       # not on the calendar
        stale = dict(cal, built="2026-11-13")
        self.assertEqual(ec.note("B", today, stale), "")                    # an old calendar says nothing
        self.assertEqual(ec.note("B", today, {}), "")

    def test_the_window_edges(self):
        """Exactly WARN_DAYS ahead is still shown; a calendar exactly MAX_AGE_DAYS old still counts."""
        today = D(2026, 11, 19)
        cal = {"built": "2026-11-19", "symbols": {"W": {"next": "2026-11-26"}, "P": {"next": "2026-11-18"}}}
        self.assertEqual(ec.WARN_DAYS, 7)
        self.assertEqual(ec.note("W", today, cal), "earnings in 7 days (2026-11-26)")
        self.assertEqual(ec.note("P", today, cal), "")                     # already reported
        self.assertEqual(ec.note("W", today, dict(cal, built="2026-11-15")), "earnings in 7 days (2026-11-26)")
        self.assertEqual(ec.note("W", today, dict(cal, built="2026-11-14")), "")   # 5 days old

    def test_a_failed_etrade_batch_keeps_the_rest(self):
        ok = {"QuoteResponse": {"QuoteData": [{"Product": {"symbol": "BABA"}, "All": {"nextEarningDate": "11/20/2026"}}]}}
        market = mock.Mock()
        market.get_quote.side_effect = [RuntimeError("401"), ok]
        client = mock.Mock()
        client.market.raw_market.return_value = market
        with mock.patch.object(ec.etrade, "ETradeClient", return_value=client) as mk, \
             self.assertLogs("aether.earnings_calendar", level="WARNING"):
            out = ec.etrade_next([f"S{i}" for i in range(25)] + ["BABA"])
        self.assertEqual(out, {"BABA": D(2026, 11, 20)})
        self.assertEqual(mk.call_args.kwargs.get("role"), "data")          # data plane: no renew, no browser
        self.assertIs(market.get_quote.call_args.kwargs.get("require_earnings_date"), True)


class WiredTest(unittest.TestCase):
    def test_the_oracle_card_shows_it(self):
        today = datetime.date.today()
        cal = {"built": today.isoformat(), "symbols": {
            "GOOGL": {"next": (today + datetime.timedelta(days=2)).isoformat()}, "SA": {"next": None}}}
        acct = {"id": "9999", "balance": 100.0, "equity": 20000.0, "holdings": []}
        buys = [{"symbol": s, "price": 10.0, "stop": 9.0, "target": 12.0, "combined": 11.0, "pgr": "Bu",
                 "patterns": ""} for s in ("GOOGL", "SA")]
        with mock.patch.object(oracle.earnings_calendar, "load", return_value=cal):
            html = oracle.generate_oracle_html(acct, [], [], buys)
        self.assertIn("earnings in 2 days", html)
        self.assertIn("earnings date unknown", html)

    def test_the_evening_email_builds_and_shows_it(self):
        src = (_REPO / "daily_task.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        names = {f"{n.func.value.id}.{n.func.attr}" for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name)}
        self.assertTrue({"earnings_calendar.build", "earnings_calendar.load", "earnings_calendar.note"} <= names)
        self.assertLess(src.index("earnings_calendar.build("), src.index("def add_rows("))


if __name__ == "__main__":
    unittest.main()
