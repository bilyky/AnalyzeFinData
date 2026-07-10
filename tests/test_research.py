"""
Tests for data_api.read_research() — the Research-sheet parser behind the
dashboard's Research page. Builds a synthetic workbook so no live data is needed.
"""
import os
import sys
import tempfile
import unittest

import openpyxl

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import data_api


def _row(**kw):
    r = [None] * 27
    m = data_api._RESEARCH
    r[m["sym"]] = kw.get("sym")
    r[m["industry"]] = kw.get("industry")
    r[m["pgr"]] = kw.get("pgr")
    r[m["setup"]] = kw.get("setup")
    r[m["winpct"]] = kw.get("winpct")
    r[m["money_flow"]] = kw.get("money_flow")
    r[m["lt_trend"]] = kw.get("lt_trend")
    r[m["s10"]] = kw.get("s10")
    r[m["l60"]] = kw.get("l60")
    r[m["patterns"]] = kw.get("patterns")
    return r


def _build(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Research"
    ws.append(["Rank", None, None, "Symb"] + [None] * 23)   # header row
    ws.append(_row(sym="AAA", industry="Software", pgr="Bu+", setup=1, winpct=0.643,
                   money_flow="Strong", lt_trend="Weak", s10=4.0, l60=6.0, patterns="MACD+"))
    ws.append(_row(sym="BBB", industry="Energy", pgr="Be-", setup=0, winpct=0.463,
                   money_flow="Neutral", lt_trend="Strong", s10=-3.0, l60=-4.0))
    wb.save(path)


class TestReadResearch(unittest.TestCase):
    def setUp(self):
        self._orig_xlsx = data_api._XLSX
        data_api._cache.clear()
        self.tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
        self.tmp.close()
        _build(self.tmp.name)
        # read_research reads from _XLSX (a Path); patch it to the temp file.
        from pathlib import Path
        data_api._XLSX = Path(self.tmp.name)

    def tearDown(self):
        data_api._XLSX = self._orig_xlsx
        data_api._cache.clear()
        os.unlink(self.tmp.name)

    def test_rows_parsed(self):
        rows = {r["symbol"]: r for r in data_api.read_research()["rows"]}
        self.assertEqual(set(rows), {"AAA", "BBB"})

    def test_setup_flag_and_combined(self):
        rows = {r["symbol"]: r for r in data_api.read_research()["rows"]}
        self.assertTrue(rows["AAA"]["setup"])
        self.assertFalse(rows["BBB"]["setup"])
        self.assertEqual(rows["AAA"]["combined"], 10.0)   # 4 + 6
        self.assertEqual(rows["BBB"]["combined"], -7.0)

    def test_winpct_scaled_to_percent(self):
        aaa = next(r for r in data_api.read_research()["rows"] if r["symbol"] == "AAA")
        self.assertEqual(aaa["win_pct"], 64.3)            # 0.643 -> 64.3

    def test_text_ratings_passthrough(self):
        aaa = next(r for r in data_api.read_research()["rows"] if r["symbol"] == "AAA")
        self.assertEqual(aaa["money_flow"], "Strong")     # not coerced to None
        self.assertEqual(aaa["lt_trend"], "Weak")

    def test_summary_counts(self):
        s = data_api.read_research()["summary"]
        self.assertEqual(s["total"], 2)
        self.assertEqual(s["setups"], 1)
        self.assertEqual(s["bullish"], 1)                 # AAA combined 10 > 0
        self.assertEqual(s["bearish"], 1)                 # BBB combined -7 < 0

    def test_header_and_blank_rows_skipped(self):
        # No row should have symbol "Symb" (the header) in the output.
        syms = [r["symbol"] for r in data_api.read_research()["rows"]]
        self.assertNotIn("Symb", syms)


if __name__ == "__main__":
    unittest.main()
