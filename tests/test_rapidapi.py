"""
Tests for rapidapi.repair_missing's force flag — no network, no API key.
_fetch_and_merge and _check_recovery are mocked.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import rapidapi


class TestRepairForce(unittest.TestCase):
    def test_current_file_skipped_without_force(self):
        # _check_recovery says "no repair needed" and there's an existing cache.
        with mock.patch.object(rapidapi, "_check_recovery", return_value=(False, {"x": 1})), \
             mock.patch.object(rapidapi, "_fetch_and_merge") as fam, \
             mock.patch.object(rapidapi.time, "sleep"):
            res = rapidapi.repair_missing(["INTC"], "2026-07-10", force=False)
        fam.assert_not_called()
        self.assertEqual(res["skipped"], 1)
        self.assertEqual(res["updated"], 0)

    def test_current_file_fetched_with_force(self):
        with mock.patch.object(rapidapi, "_check_recovery", return_value=(False, {"x": 1})), \
             mock.patch.object(rapidapi, "_fetch_and_merge") as fam, \
             mock.patch.object(rapidapi.time, "sleep"):
            res = rapidapi.repair_missing(["INTC"], "2026-07-10", force=True)
        fam.assert_called_once()
        # existing cache present -> compact fetch (not a full re-download)
        self.assertEqual(fam.call_args.kwargs.get("outputsize"), "compact")
        self.assertEqual(res["updated"], 1)

    def test_missing_file_full_fetch_even_without_force(self):
        # No cache -> needs repair regardless of force, and uses a full fetch.
        with mock.patch.object(rapidapi, "_check_recovery", return_value=(True, None)), \
             mock.patch.object(rapidapi, "_fetch_and_merge") as fam, \
             mock.patch.object(rapidapi.time, "sleep"):
            rapidapi.repair_missing(["NEW"], "2026-07-10", force=False)
        self.assertEqual(fam.call_args.kwargs.get("outputsize"), "full")


if __name__ == "__main__":
    unittest.main()
