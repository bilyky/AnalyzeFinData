"""A batch's per-item failures are WARNINGs; one summary is an ERROR (the recovery email) only when
most of the batch failed. PROD 11-02: two of ~400 RapidAPI fetches reached the recovery email."""
import datetime
import os
import sys
import tempfile
import unittest
from unittest import mock

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import powergauge
from aether import batch_failures as bf
from tests.test_chaikin_fail_fast import TestRunHistoryStops


class TestRule(unittest.TestCase):
    def test_most_failed(self):
        cases = [(0, 400, False), (2, 400, False), (3, 4, True), (2, 4, False), (3, 3, False), (201, 400, True)]
        for failed, attempted, want in cases:
            with self.subTest(failed=failed, attempted=attempted):
                self.assertEqual(bf.most_failed(failed, attempted), want)

    def test_one_line_at_the_right_level(self):
        log = mock.MagicMock()
        self.assertFalse(bf.log_batch_failures(log, "[x]", ["ILMN", "BSY"], 400))
        log.error.assert_not_called()
        self.assertIn("2 of 400 failed: ILMN, BSY", log.warning.call_args.args[0])
        log = mock.MagicMock()
        self.assertTrue(bf.log_batch_failures(log, "[x]", ["A", "B", "C"], 4))
        self.assertIn("(most of the batch)", log.error.call_args.args[0])
        log = mock.MagicMock()
        self.assertFalse(bf.log_batch_failures(log, "[x]", [], 4))
        log.warning.assert_not_called()
        log.error.assert_not_called()


class TestRunHistory(unittest.TestCase):
    _run = TestRunHistoryStops._run              # the same mocked backfill, not its tests

    def _errors(self, fail):
        ok = mock.MagicMock(price=10.0)

        def fetch(sym):
            if sym in fail:
                raise requests.ConnectionError("Read timed out")
            return ok
        with mock.patch("run_history._log") as log:
            _calls, err = self._run(fetch)
        self.assertIsNone(err)
        return log

    def test_one_failed_symbol_is_a_warning(self):
        log = self._errors({"B"})
        log.error.assert_not_called()
        self.assertTrue(any("B" in str(c) for c in log.warning.call_args_list))

    def test_a_day_where_most_fail_is_one_error_per_day(self):
        log = self._errors({"A", "B", "C"})          # 3 of 4 per day, 2 days (10-22 DNS outage)
        self.assertEqual(log.error.call_count, 2)
        self.assertIn("3 of 4 failed (most of the batch)", log.error.call_args.args[0])


class TestScreen(unittest.TestCase):
    def _screen(self, fail):
        with tempfile.TemporaryDirectory() as td:
            syms = os.path.join(td, "symbols_to_check.txt")
            with open(syms, "w") as f:
                f.write("1 AAA\n2 BBB\n3 CCC\n4 DDD\n")
            good = powergauge.PowerGauge("AAA", datetime.date(2026, 10, 27))

            def fetch(sym, d, prefer):
                if sym in fail:
                    raise requests.ConnectionError("Max retries exceeded")
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
                 mock.patch.object(powergauge, "_pg_log") as log:
                powergauge.check_from_file(prefer_cache=False, date=datetime.date(2026, 10, 27))
        return log

    def test_one_failed_symbol_is_a_warning_most_failing_is_one_error(self):
        log = self._screen({"BBB"})
        log.error.assert_not_called()
        log = self._screen({"AAA", "BBB", "CCC"})
        self.assertEqual(log.error.call_count, 1)
        self.assertIn("[Chaikin fetch]: 3 of 4 failed", log.error.call_args.args[0])


class TestXlsScreen(unittest.TestCase):
    """check_from_xls is the screen PROD uses (symbols_to_check.txt is retired); #227 review 🟠."""

    def _screen(self, fail):
        import openpyxl
        with tempfile.TemporaryDirectory() as td:
            book = os.path.join(td, "state_of_the_day.xlsx")
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Research"
            ws.append(["#", "", "", "Symbol"])
            for i, sym in enumerate(("AAA", "BBB", "CCC", "DDD"), 1):
                ws.append([i, None, None, sym])
            wb.save(book)

            def fetch(sym, d, prefer):
                if sym in fail:
                    raise requests.ConnectionError("Max retries exceeded")
                pg = powergauge.PowerGauge(sym, d)
                pg.price = -1                      # "no data": the write phase skips the row
                return pg
            with mock.patch.object(powergauge, "XLSX_FILE", book),                  mock.patch.object(powergauge, "SRC_XLSX", book),                  mock.patch("powergauge._backup_xlsx", return_value=None),                  mock.patch("powergauge._build_cache_index"),                  mock.patch("powergauge.login", return_value={"jsessionid": "x"}),                  mock.patch("powergauge.get_symbol_data", side_effect=fetch),                  mock.patch.object(powergauge, "_pg_log") as log:
                try:
                    powergauge.check_from_xls(prefer_cache=False, date=datetime.date(2026, 10, 27))
                except Exception:
                    pass   # only the fetch phase and its batch line are under test
        return log

    def test_one_failed_symbol_is_a_warning_most_failing_is_one_error(self):
        log = self._screen({"BBB"})
        self.assertFalse([c for c in log.error.call_args_list if "[Chaikin fetch]" in str(c)])
        self.assertTrue([c for c in log.warning.call_args_list if "[Chaikin fetch]: 1 of 4 failed" in str(c)])
        log = self._screen({"AAA", "BBB", "CCC"})
        lines = [str(c) for c in log.error.call_args_list if "[Chaikin fetch]" in str(c)]
        self.assertEqual(len(lines), 1)
        self.assertIn("3 of 4 failed (most of the batch)", lines[0])


    def test_no_data_symbols_get_one_line_not_one_per_row(self):
        # PROD 11-02..11-04: the same 17 no-data symbols logged a warning each per refresh.
        log = self._screen({"AAA", "BBB", "CCC"})          # DDD: fetched, but no data
        per_row = [c for c in log.warning.call_args_list if "row skipped" in str(c)]
        self.assertEqual(per_row, [])
        lines = [str(c) for c in log.warning.call_args_list if "[Chaikin] no market data" in str(c)]
        self.assertEqual(len(lines), 1)
        self.assertIn("1 of 4 failed", lines[0])        # fetch failures are not counted twice
        self.assertIn("DDD", lines[0])

    def test_all_no_data_is_one_error(self):
        log = self._screen(set())
        lines = [str(c) for c in log.error.call_args_list if "[Chaikin] no market data" in str(c)]
        self.assertEqual(len(lines), 1)
        self.assertIn("4 of 4 failed (most of the batch)", lines[0])


class TestSchemaWarning(unittest.TestCase):
    def _check(self, data):
        with mock.patch.object(powergauge, "_pg_log") as log:
            powergauge.PowerGauge("IRBT", datetime.date(2026, 11, 3))._check_schema(data)
        return log

    def test_empty_payload_is_no_data_not_a_schema_change(self):
        log = self._check({})
        log.warning.assert_not_called()
        self.assertIn("SCHEMA WARNING", log.debug.call_args.args[0])

    def test_partial_payload_still_warns(self):
        log = self._check({"pgr": [], "metaInfo": [{"Last": 1}], "checklist_stocks": {}})
        self.assertIn("SCHEMA WARNING", log.warning.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
