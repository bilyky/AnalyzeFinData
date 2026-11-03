"""The end-of-run summary names failed symbols once, in one line.

PROD 10-02 / 10-05: every failed fetch was logged twice: once when it happened
("[RapidAPI] V: ERROR - 429 ...") and again in an end-of-run loop with one ERROR line per
symbol at the same timestamp (64/64, 61/61, 109/109 duplicates).
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import rapidapi


class TestRunSummary(unittest.TestCase):
    def _summary(self, errors, quota=False, updated=10, held=()):
        result = {"updated": updated, "skipped": 2, "errors": errors, "deferred": 5,
                  "quota_stopped": quota, "failed_priority": list(held)}
        with mock.patch.object(rapidapi, "_log") as log:
            rapidapi.log_run_summary(result)
        return log

    def test_errors_are_one_line_not_one_per_symbol(self):
        log = self._summary([("V", "429 Too Many Requests"), ("XLU", "429 Too Many Requests")])
        lines = [c.args[0] for c in log.warning.call_args_list + log.error.call_args_list]
        self.assertEqual(len(lines), 1)
        self.assertIn("2 symbol(s) failed", lines[0])
        self.assertIn("V, XLU", lines[0])

    def test_clean_run_logs_no_error(self):
        log = self._summary([])
        log.error.assert_not_called()
        log.warning.assert_not_called()
        done = log.info.call_args.args[0] % log.info.call_args.args[1:]
        self.assertIn("10 fetched", done)

    def test_error_only_when_it_needs_a_person(self):
        # PROD 11-02: two symbols out of a ~400 fetch pass (a timeout and Alpha Vantage's burst
        # guard) reached the recovery email. They are retried next run; alert only when it matters.
        two = [("ILMN", "Burst pattern detected"), ("BSY", "Read timed out")]
        cases = [
            ("a few failed, retried next run", dict(errors=two, updated=398), "warning"),
            ("a held position failed", dict(errors=two, updated=398, held=["BSY"]), "error"),
            ("the quota stopped the pass", dict(errors=two, updated=398, quota=True), "error"),
            ("most of the pass failed", dict(errors=two * 3, updated=2), "error"),
            ("one heal attempt failed", dict(errors=two[:1], updated=0), "warning"),
        ]
        for name, kw, level in cases:
            with self.subTest(name):
                log = self._summary(**kw)
                self.assertEqual((log.error.call_count, log.warning.call_count),
                                 (1, 0) if level == "error" else (0, 1))
        log = self._summary(two, updated=398, held=["BSY"])
        self.assertIn("held positions among them: BSY", log.error.call_args.args[0])


class TestPerSymbolFailureIsAWarning(unittest.TestCase):
    def test_failed_fetch_logs_a_warning_and_records_a_held_failure(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td,              mock.patch.object(rapidapi, "OHLCV_DIR", td),              mock.patch.object(rapidapi, "_fetch_and_merge", side_effect=RuntimeError("Read timed out")),              mock.patch("rapidapi.time.sleep"),              mock.patch.object(rapidapi, "_log") as log:
            res = rapidapi.repair_missing(["BSY", "ILMN"], "2026-11-02", force=True, priority={"BSY"})
        log.error.assert_not_called()
        self.assertEqual(log.warning.call_count, 2)
        self.assertEqual(res["failed_priority"], ["BSY"])


if __name__ == "__main__":
    unittest.main()
