"""The dashboard's per-position pricing warning is logged once per symbol, kind and day.

PROD 2026-09-30 .. 10-08: read_portfolio() runs on every dashboard refresh (about once a
minute) and re-logged the same "[PRICING] Game position X has discrepancy: Flat placeholder"
warning up to 398 times per symbol per day (4,491 lines). The check itself still runs every
time; only the repeat of an identical warning is dropped.
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import data_api

_FLAT = data_api.PricingDiscrepancyError(
    "🛑 [PRICING DISCREPANCY] Flat placeholder detected for ULTA!\n  Date: 2026-09-29")
_3WAY = data_api.PricingDiscrepancyError(
    "🛑 [PRICING DISCREPANCY] 3-Way Pricing discrepancy detected for ULTA!\n  Active Price: $1")


class TestPricingWarningDedupe(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        game = Path(self.tmp.name) / "ai_portfolio_game.json"
        game.write_text(json.dumps({"balance": 1000.0, "history": [],
                                    "positions": {"ULTA": {"qty": 1, "cost": 500.0, "stop_loss": 450.0}}}))
        for p in (mock.patch.object(data_api, "_GAME", game),
                  mock.patch.object(data_api, "_load_latest_close_from_cache", return_value=549.13)):
            p.start()
            self.addCleanup(p.stop)
        data_api._warned_today.clear()
        self.addCleanup(data_api._warned_today.clear)

    def _refresh(self, err, today=date(2026, 9, 30)):
        with mock.patch.object(data_api, "verify_price_integrity", side_effect=err), \
             mock.patch.object(data_api, "_today", return_value=today), \
             mock.patch.object(data_api, "_log") as log:
            data_api.read_portfolio()
        return [str(c.args[0]) for c in log.warning.call_args_list if "Game position" in str(c.args[0])]

    def test_same_warning_twice_in_a_day_is_logged_once(self):
        self.assertEqual(len(self._refresh(_FLAT)), 1)
        self.assertEqual(self._refresh(_FLAT), [])

    def test_a_new_day_or_a_new_kind_logs_again(self):
        self.assertEqual(len(self._refresh(_FLAT)), 1)
        self.assertEqual(len(self._refresh(_3WAY)), 1)
        self.assertEqual(len(self._refresh(_FLAT, today=date(2026, 10, 1))), 1)


if __name__ == "__main__":
    unittest.main()
