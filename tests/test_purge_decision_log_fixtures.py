"""purge_decision_log_fixtures: drop rows priced far from the real close; back up before writing."""
import io
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


class TestRepeatedSignature(unittest.TestCase):
    def test_same_price_and_cost_on_five_dates_is_a_fixture(self):
        gmed = [_row("GMED", f"2026-09-{d:02d}", 72.0) | {"cost": 74.8} for d in range(1, 6)]
        real = [_row("ABC", f"2026-09-{d:02d}", 50.0 + d) for d in range(1, 6)]   # price moves
        weekend = [_row("XYZ", d, 20.0) for d in ("2026-09-04", "2026-09-05", "2026-09-06")]
        keep, drop = pf.split(gmed + real + weekend)
        self.assertEqual(sorted({e["symbol"] for e in drop}), ["GMED"])
        self.assertEqual(len(keep), 8)


class TestApplySafety(unittest.TestCase):
    def _setup(self, td, extra_lines=()):
        ohlcv = os.path.join(td, "Symbol_full")
        os.makedirs(ohlcv)
        with open(os.path.join(ohlcv, "ULTA_daily.json"), "w") as f:
            json.dump({"Time Series (Daily)": {"2026-09-04": {"4. close": "557.86"}}}, f)
        log = os.path.join(td, "decision_log.jsonl")
        with open(log, "w") as f:
            f.write(json.dumps(_row("ULTA", "2026-09-04", 95.0)) + "\n")
            f.write(json.dumps(_row("ULTA", "2026-09-04", 556.0)) + "\n")
            for line in extra_lines:
                f.write(line + "\n")
        return log

    def test_malformed_lines_are_counted_in_the_report(self):
        with tempfile.TemporaryDirectory() as td:
            log = self._setup(td, ['{"half": '])
            out = io.StringIO()
            with mock.patch.dict(os.environ, {"AETHER_CACHE_DIR": td, "AETHER_DATA_DIR": td}),                  mock.patch.object(sys, "stdout", out):
                pf.main(["--log", log])
            self.assertIn("3 lines, 2 rows, 1 fixture rows, 1 kept, 1 malformed line(s) also dropped", out.getvalue())

    def test_apply_aborts_when_the_log_changes_mid_run(self):
        with tempfile.TemporaryDirectory() as td:
            log = self._setup(td)
            real_split = pf.split

            def split_then_append(entries):
                with open(log, "a") as f:            # the trade run appends meanwhile
                    f.write(json.dumps(_row("MSCI", "2026-09-05", 600.0)) + "\n")
                return real_split(entries)
            with mock.patch.dict(os.environ, {"AETHER_CACHE_DIR": td, "AETHER_DATA_DIR": td}),                  mock.patch.object(sys, "stdout"), mock.patch.object(pf, "split", side_effect=split_then_append):
                self.assertEqual(pf.main(["--log", log, "--apply"]), 1)
            with open(log) as f:
                self.assertEqual(len(f.readlines()), 3)   # untouched, the appended row kept
            self.assertFalse(os.path.exists(log + ".tmp"))


if __name__ == "__main__":
    unittest.main()
