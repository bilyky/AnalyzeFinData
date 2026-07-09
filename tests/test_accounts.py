"""
Tests for data_api.read_accounts() — the Short_Long two-table parser.
Builds a synthetic workbook so the test doesn't depend on live data.
"""
import os
import sys
import tempfile
import unittest

import openpyxl

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import data_api


def _build_sheet(path):
    """Two Symb-headed tables separated by blanks, second table with a leading blank row
    (mirrors the real Short_Long layout)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Short_Long"
    # Column layout: 1=Symb 2=Qty 3=Buy 4=Top 5=Target 6=Stop ... 17=S10 18=L60 19=Win% 20=Status 23=InProfit
    def row(sym=None, qty=None, buy=None, top=None, target=None, stop=None,
            s10=None, l60=None, win=None, status=None, inp=None):
        r = [None] * 24
        r[1], r[2], r[3], r[4], r[5], r[6] = sym, qty, buy, top, target, stop
        r[16], r[17], r[18], r[19], r[22] = s10, l60, win, status, inp
        return r

    ws.append([None] * 24)                                    # row 1: spacer
    ws.append(row(sym="Symb"))                                # row 2: T1 header
    ws.append(row("PATH", 56, 18.7, 20.0, 30, 25, 8.5, 9.5, "64%", "STRONG HOLD", "YES"))
    ws.append(row("EVTV", 170, 5.88, 1.45, 15, 8, 0.5, -2.0, "50%", "REDUCE", "NO"))
    ws.append([None] * 24); ws.append([None] * 24); ws.append([None] * 24)  # 3 blank separator
    ws.append(row(sym="Symb"))                                # T2 header
    ws.append([None] * 24)                                    # leading blank inside T2
    ws.append(row("PRTS", 1370, 3.79, 6.33, 5, 3.8, -4.2, -1.6, "50%", "REDUCE", "YES"))
    wb.save(path)


class TestReadAccounts(unittest.TestCase):
    def setUp(self):
        self._orig_xlsx = data_api._XLSX
        self._orig_real_acct_ids = data_api._real_acct_ids
        data_api._real_acct_ids = lambda: ["ACCT_A", "ACCT_B"]
        data_api._cache.clear()
        self.tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
        self.tmp.close()
        _build_sheet(self.tmp.name)
        data_api._XLSX = self.tmp.name

    def tearDown(self):
        data_api._XLSX = self._orig_xlsx
        data_api._real_acct_ids = self._orig_real_acct_ids
        data_api._cache.clear()
        os.unlink(self.tmp.name)

    def test_three_accounts_returned(self):
        accts = data_api.read_accounts()["accounts"]
        ids = [a["id"] for a in accts]
        self.assertEqual(ids, ["ACCT_A", "ACCT_B", "game"])

    def test_real_account_types_and_counts(self):
        accts = {a["id"]: a for a in data_api.read_accounts()["accounts"]}
        self.assertEqual(accts["ACCT_A"]["type"], "real")
        self.assertEqual(accts["ACCT_A"]["count"], 2)   # PATH, EVTV
        self.assertEqual(accts["ACCT_B"]["count"], 1)   # PRTS (leading blank skipped)

    def test_leading_blank_in_second_table_skipped(self):
        accts = {a["id"]: a for a in data_api.read_accounts()["accounts"]}
        syms = [h["symbol"] for h in accts["ACCT_B"]["holdings"]]
        self.assertEqual(syms, ["PRTS"])

    def test_pnl_and_total_computed(self):
        accts = {a["id"]: a for a in data_api.read_accounts()["accounts"]}
        path = accts["ACCT_A"]["holdings"][0]
        self.assertEqual(path["symbol"], "PATH")
        self.assertAlmostEqual(path["pnl"], (20.0 - 18.7) * 56, places=2)
        self.assertEqual(path["total"], 18.0)          # 8.5 + 9.5
        self.assertEqual(path["status"], "STRONG HOLD")

    def test_game_account_present(self):
        game = [a for a in data_api.read_accounts()["accounts"] if a["id"] == "game"][0]
        self.assertEqual(game["type"], "game")
        self.assertIn("equity", game)


if __name__ == "__main__":
    unittest.main()
