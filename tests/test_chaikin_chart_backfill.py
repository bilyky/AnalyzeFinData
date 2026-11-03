"""Tests for scripts/utils/chaikin_chart_backfill.py: field selection, merge, the content
check, resume/stop behaviour of the run loop (fake fetch, no network) and the report."""
import datetime
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "utils"))
import chaikin_chart_backfill as bf  # noqa: E402


def _row(day, pgr=5, close=100.0):
    return {"date": day, "symbol": "X", "dailyPgr": pgr, "weeklyPgr": pgr, "cmf": 3.2, "relativeStrength": 0.4,
            "cycle": 50.0, "chaikinTrend20001": 99.0, "upperBand": 110.0, "lowerBand": 90.0, "close": close,
            "open": 99.0, "high": 101.0, "low": 98.0, "volume": 1000, "breakoutBuy": 0, "epsActualEps": None}


class TestSlimMergeCheck(unittest.TestCase):
    def test_slim_keeps_only_the_useful_fields(self):
        rows = bf.slim([_row("2026-11-02"), {"no": "date"}, "junk"])
        self.assertEqual(list(rows), ["2026-11-02"])
        self.assertEqual(set(rows["2026-11-02"]), set(bf.KEEP))
        self.assertNotIn("volume", rows["2026-11-02"])

    def test_merge_new_wins_old_kept(self):
        m = bf.merge({"d1": {"dailyPgr": 1}, "d2": {"dailyPgr": 2}}, {"d2": {"dailyPgr": 7}, "d3": {"dailyPgr": 3}})
        self.assertEqual({k: v["dailyPgr"] for k, v in m.items()}, {"d1": 1, "d2": 7, "d3": 3})

    def test_check_flags_bad_pgr_and_close_mismatch(self):
        rows = bf.slim([_row("2026-10-30", pgr=9), _row("2026-11-02", close=110.0)])
        c = bf.check(rows, {"2026-10-30": 100.0, "2026-11-02": 100.0})
        self.assertEqual((c["rows"], c["rated_days"], c["pgr_out_of_range"], c["close_shared"]), (2, 2, 1, 2))
        self.assertEqual(len(c["problems"]), 2)
        ok = bf.check(bf.slim([_row("2026-11-02", pgr=0)]), {"2026-11-02": 100.2})   # 0 = unrated, fine
        self.assertEqual((ok["problems"], ok["rated_days"]), ([], 0))
        self.assertEqual(bf.check({}, {})["problems"], ["no rows"])


class TestRun(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._p = mock.patch.object(bf, "chart_dir", return_value=self._tmp.name)
        self._p.start()
        self._o = mock.patch.object(bf, "_out")
        self._o.start()

    def tearDown(self):
        self._o.stop()
        self._p.stop()
        self._tmp.cleanup()

    def _saved(self, sym):
        with open(os.path.join(self._tmp.name, f"{sym}_chart.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_fetch_save_merge_and_skip_today(self):
        with open(os.path.join(self._tmp.name, "AAA_chart.json"), "w", encoding="utf-8") as f:
            json.dump({"fetched": "2026-01-01", "rows": {"2021-11-02": {"dailyPgr": 2}}}, f)
        calls = []

        def fake(sym):
            calls.append(sym)
            return [_row("2026-11-02")]

        r = bf.run(["AAA", "BBB"], fetch_fn=fake, sleep=0)
        self.assertEqual((r["fetched"], calls), (["AAA", "BBB"], ["AAA", "BBB"]))
        saved = self._saved("AAA")
        self.assertEqual(sorted(saved["rows"]), ["2021-11-02", "2026-11-02"])     # older rows kept
        self.assertEqual(saved["fetched"], datetime.date.today().isoformat())
        r2 = bf.run(["AAA", "BBB"], fetch_fn=fake, sleep=0)                      # restart: nothing re-fetched
        self.assertEqual((r2["skipped"], len(calls)), (["AAA", "BBB"], 2))
        bf.run(["AAA"], force=True, fetch_fn=fake, sleep=0)
        self.assertEqual(len(calls), 3)

    def test_auth_error_stops_at_once(self):
        def fake(sym):
            if sym == "BBB":
                raise bf.AuthError("HTTP 401")
            return [_row("2026-11-02")]

        r = bf.run(["AAA", "BBB", "CCC"], fetch_fn=fake, sleep=0)
        self.assertEqual(r["fetched"], ["AAA"])
        self.assertIn("auth error on BBB", r["stopped"])

    def test_three_errors_in_a_row_stop_but_one_does_not(self):
        bad = {"B1", "C1", "C2", "C3"}

        def fake(sym):
            if sym in bad:
                raise RuntimeError("timeout")
            return [] if sym == "EMPTY" else [_row("2026-11-02")]

        r = bf.run(["B1", "OK1", "EMPTY", "C1", "C2", "C3", "OK2"], fetch_fn=fake, sleep=0)
        self.assertEqual(r["fetched"], ["OK1"])
        self.assertEqual(r["failed"], ["B1", "EMPTY", "C1", "C2", "C3"])
        self.assertIn("3 errors in a row", r["stopped"])

    def test_report_and_exit_code(self):
        bf.run(["AAA"], fetch_fn=lambda s: [_row("2026-11-02", pgr=9)], sleep=0)
        with mock.patch.object(bf, "rapid_closes", return_value={}):
            rep = bf.report(["AAA", "NONE"])
        self.assertEqual(list(rep), ["AAA"])
        self.assertTrue(rep["AAA"]["problems"])
        self.assertTrue(os.path.exists(os.path.join(self._tmp.name, "_report.json")))
        with mock.patch.object(bf, "run", return_value={"fetched": [], "skipped": [], "failed": [],
                                                       "stopped": "auth error"}), \
                mock.patch.object(bf, "rapid_closes", return_value={}):
            self.assertEqual(bf.main(["--symbols", "AAA"]), 1)
            self.assertEqual(bf.main(["--symbols", "AAA", "--report"]), 0)


if __name__ == "__main__":
    unittest.main()
