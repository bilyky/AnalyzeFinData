"""Tests for scripts/utils/snapshot_integrity.py on a synthetic cache: wrong-date batches
(caught by price, or by PGR vs the chart), clean late batches kept, unverifiable ones
counted, weekday holidays vs weekends, and --apply moving files with a manifest."""
import datetime
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "utils"))
import snapshot_integrity as si  # noqa: E402

DAYS = ["2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05", "2026-03-06"]   # Mon-Fri sessions
REAL = {"2026-03-02": 10.0, "2026-03-03": 11.0, "2026-03-04": 12.0, "2026-03-05": 13.0, "2026-03-06": 14.0}
CHART = {"2026-02-27": 1, "2026-03-02": 2, "2026-03-03": 6, "2026-03-04": 7, "2026-03-05": 1,
         "2026-03-06": 2}                                                       # 7-level
PREV5 = {"2026-03-02": 1, "2026-03-03": 2, "2026-03-04": 4, "2026-03-05": 5, "2026-03-06": 1}   # D-1, 5-level


def _snap(last, pgr):
    return {"status": "ok", "metaInfo": [{"Last": last}],
            "pgr": [{"PGR Value": pgr}, {}, {}, {}, {}, {"Corrected PGR Value": pgr}, {}]}


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        t = self._tmp.name
        self.base = os.path.join(t, "Symbol")
        os.makedirs(os.path.join(t, "Symbol_full"))
        os.makedirs(os.path.join(t, "Symbol_chart"))
        self._p = [mock.patch.object(si.paths, "ohlcv_dir", return_value=os.path.join(t, "Symbol_full")),
                   mock.patch.object(si.paths, "cache_dir", return_value=t),
                   mock.patch.object(si.paths, "data_dir", return_value=t),
                   mock.patch.object(si, "_out")]
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in self._p:
            p.stop()
        self._tmp.cleanup()

    def write(self, sym, day, snap, written: str):
        os.makedirs(os.path.join(self.base, sym), exist_ok=True)
        p = os.path.join(self.base, sym, f"{sym}_{day}.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(snap, f)
        ts = time.mktime(datetime.datetime.fromisoformat(written + " 12:00").timetuple())
        os.utime(p, (ts, ts))
        return p

    def references(self, sym, closes=True, chart=True):
        if closes:
            with open(os.path.join(self._tmp.name, "Symbol_full", f"{sym}_daily.json"), "w") as f:
                json.dump({"Time Series (Daily)": {d: {"4. close": str(c)} for d, c in REAL.items()}}, f)
        if chart:
            with open(os.path.join(self._tmp.name, "Symbol_chart", f"{sym}_chart.json"), "w") as f:
                json.dump({"rows": {d: {"dailyPgr": v} for d, v in CHART.items()}}, f)


class TestDetect(Base):
    def test_on_time_files_are_fine(self):
        self.references("AAA")
        for d in DAYS:
            self.write("AAA", d, _snap(REAL[d], PREV5[d]), d)
        r = si.scan_symbol(self.base, "AAA")
        self.assertEqual((r["wrong_date"], r["late"]), ([], 0))

    def test_backfill_with_one_current_price_is_caught(self):
        self.references("AAA", chart=False)
        ps = [self.write("AAA", d, _snap(50.0, 3), "2026-05-08") for d in DAYS]
        self.assertEqual(sorted(si.scan_symbol(self.base, "AAA")["wrong_date"]), sorted(ps))

    def test_repaired_prices_but_constant_pgr_is_caught(self):
        self.references("AAA")
        ps = [self.write("AAA", d, _snap(REAL[d], 5), "2026-07-24") for d in DAYS]   # right price, PGR stuck
        self.assertEqual(sorted(si.scan_symbol(self.base, "AAA")["wrong_date"]), sorted(ps))

    def test_late_batch_that_checks_out_is_kept(self):
        self.references("AAA")
        for d in DAYS:
            self.write("AAA", d, _snap(REAL[d], PREV5[d]), "2026-07-24")   # a copy/restore, content right
        r = si.scan_symbol(self.base, "AAA")
        self.assertEqual((r["wrong_date"], r["late_passed"]), ([], 5))

    def test_unverifiable_and_small_batches(self):
        for d in DAYS:
            self.write("NOREF", d, _snap(50.0, 3), "2026-05-08")
        self.assertEqual(si.scan_symbol(self.base, "NOREF")["unverified"], 5)
        self.references("TWO", chart=False)
        for d in DAYS[:2]:                                                  # under BATCH_MIN dates
            self.write("TWO", d, _snap(50.0, 3), "2026-05-08")
        self.assertEqual(si.scan_symbol(self.base, "TWO")["wrong_date"], [])

    def test_check_edges(self):
        self.assertEqual(si.file_checks(_snap(10.19, 1), "2026-03-02", REAL, {}), {"price": True})
        self.assertEqual(si.file_checks(_snap(10.21, 1), "2026-03-02", REAL, {}), {"price": False})
        self.assertFalse(si.batch_is_wrong([{"price": False}] + [{"price": True}] * 2))   # 1 of 3
        self.assertTrue(si.batch_is_wrong([{"price": False}] * 2 + [{"price": True}] * 2))   # half
        self.assertFalse(si.batch_is_wrong([{"pgr": False}] * 4))           # under PGR_MIN checks
        self.assertTrue(si.batch_is_wrong([{"pgr": False}] + [{"pgr": True}] * 4))   # 1 of 5 = 20%
        self.assertFalse(si.batch_is_wrong([{"pgr": False}] + [{"pgr": True}] * 5))  # 1 of 6

    def test_weekday_holiday_vs_weekend(self):
        self.write("AAA", "2026-05-25", _snap(1, 1), "2026-05-25")          # Memorial Day
        self.write("AAA", "2026-05-23", _snap(1, 1), "2026-05-23")          # Saturday
        r = si.scan_symbol(self.base, "AAA")
        self.assertEqual([os.path.basename(p) for p in r["holiday"]], ["AAA_2026-05-25.json"])


class TestApply(Base):
    def test_dry_run_moves_nothing_apply_moves_with_manifest(self):
        self.references("AAA", chart=False)
        for d in DAYS:
            self.write("AAA", d, _snap(50.0, 3), "2026-05-08")
        self.write("AAA", "2026-05-25", _snap(1, 1), "2026-05-25")
        self.write("AAA", "2026-03-09", _snap(1, 1), "2026-03-09")          # on time, stays
        with mock.patch.object(si.paths, "symbol_dir", return_value=self.base):
            si.main([])
            self.assertEqual(len(os.listdir(os.path.join(self.base, "AAA"))), 7)
            si.main(["--apply"])
        self.assertEqual(os.listdir(os.path.join(self.base, "AAA")), ["AAA_2026-03-09.json"])
        backup = os.path.join(self._tmp.name, "Backup")
        qdir = [x for x in os.listdir(backup) if x.startswith("snapshot_quarantine_")][0]
        with open(os.path.join(backup, qdir, "manifest.json"), encoding="utf-8") as f:
            kinds = sorted(m["kind"] for m in json.load(f)["moved"])
        self.assertEqual(kinds, ["holiday"] + ["wrong_date"] * 5)
        self.assertTrue(os.path.exists(os.path.join(backup, qdir, "holiday", "AAA", "AAA_2026-05-25.json")))


if __name__ == "__main__":
    unittest.main()
