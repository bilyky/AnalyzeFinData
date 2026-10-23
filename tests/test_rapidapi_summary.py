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
    def _summary(self, errors, quota=False):
        result = {"updated": 10, "skipped": 2, "errors": errors, "deferred": 5, "quota_stopped": quota}
        with mock.patch.object(rapidapi, "_log") as log:
            rapidapi.log_run_summary(result)
        return log

    def test_errors_are_one_line_not_one_per_symbol(self):
        log = self._summary([("V", "429 Too Many Requests"), ("XLU", "429 Too Many Requests")])
        self.assertEqual(log.error.call_count, 1)
        line = log.error.call_args.args[0] % log.error.call_args.args[1:]
        self.assertIn("2 symbol(s) failed", line)
        self.assertIn("V, XLU", line)

    def test_clean_run_logs_no_error(self):
        log = self._summary([])
        log.error.assert_not_called()
        done = log.info.call_args.args[0] % log.info.call_args.args[1:]
        self.assertIn("10 fetched", done)


if __name__ == "__main__":
    unittest.main()
