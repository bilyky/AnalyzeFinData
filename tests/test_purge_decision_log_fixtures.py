"""purge_decision_log_fixtures: drop rows priced far from the real close; back up before writing."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "utils"))
import purge_decision_log_fixtures as pf


def _row(sym, date, price):
    return {"date": date, "symbol": sym, "price": price, "cost": 100, "rules_action": "HOLD"}


class TestIsFixture(unittest.TestCase):
    CLOSES = {"2026-09-04": 557.86, "2026-09-08": 540.0}

    def test_far_from_close_is_a_fixture_near_is_real(self):
        self.assertTrue(pf.is_fixture(_row("ULTA", "2026-09-04", 450.0), self.CLOSES))   # 19% off
        self.assertFalse(pf.is_fixture(_row("ULTA", "2026-09-04", 540.0), self.CLOSES))  # 3% off

    def test_weekend_row_uses_the_latest_earlier_close(self):
        self.assertTrue(pf.is_fixture(_row("ULTA", "2026-09-06", 95.0), self.CLOSES))
        self.assertEqual(pf.close_on_or_before(self.CLOSES, "2026-09-06"), 557.86)

    def test_no_earlier_close_keeps_the_row(self):
        self.assertFalse(pf.is_fixture(_row("ULTA", "2026-01-01", 95.0), self.CLOSES))
        self.assertFalse(pf.is_fixture(_row("NEW", "2026-09-04", 95.0), {}))


class TestApply(unittest.TestCase):
    def test_apply_backs_up_then_keeps_only_real_rows(self):
        with tempfile.TemporaryDirectory() as td:
            ohlcv = os.path.join(td, "Symbol_full")
            os.makedirs(ohlcv)
            with open(os.path.join(ohlcv, "ULTA_daily.json"), "w") as f:
                json.dump({"Time Series (Daily)": {"2026-09-04": {"4. close": "557.86"}}}, f)
            log = os.path.join(td, "decision_log.jsonl")
            rows = [_row("ULTA", "2026-09-04", 95.0), _row("ULTA", "2026-09-04", 556.0)]
            with open(log, "w") as f:
                f.writelines(json.dumps(r) + "\n" for r in rows)
            with mock.patch.dict(os.environ, {"AETHER_CACHE_DIR": td, "AETHER_DATA_DIR": td}), \
                 mock.patch.object(sys, "stdout"):
                self.assertEqual(pf.main(["--log", log]), 0)            # dry run first
                with open(log) as f:
                    self.assertEqual(len(f.readlines()), 2)
                self.assertEqual(pf.main(["--log", log, "--apply"]), 0)
            with open(log) as f:
                kept = [json.loads(line) for line in f]
            self.assertEqual([r["price"] for r in kept], [556.0])
            backups = [os.path.join(dp, n) for dp, _, ns in os.walk(os.path.join(td, "Backup")) for n in ns]
            self.assertEqual(len(backups), 1)
            with open(backups[0]) as f:
                self.assertEqual(len(f.readlines()), 2)


if __name__ == "__main__":
    unittest.main()
