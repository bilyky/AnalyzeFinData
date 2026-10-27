"""Unit tests for scripts/monitoring/option_chain_snapshot.py.

Covers the ban-safe collector's pure serialization + append path and the fixture-backed --dry-run.
Offline, deterministic: OUT_DIR is redirected to a temp dir; the LIVE snapshot() path is never
exercised (it needs a broker client) — only the serialization and the offline dry-run, which is the
only part that runs without network/tokens.
"""
import datetime
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pytz

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "monitoring"))
import option_chain_snapshot as snap                # noqa: E402
from aether import options_adviser as oa            # noqa: E402


class TestSerializeQuote(unittest.TestCase):
    def test_date_expiry_to_iso(self):
        q = oa.OptionQuote(option_type="CALL", strike=100.0, bid=1.0, ask=1.2, last=1.1,
                           delta=0.5, expiry=datetime.date(2026, 10, 16))
        d = snap._serialize_quote(q)
        self.assertEqual(d["expiry"], "2026-10-16")     # date -> ISO string (JSON-safe)
        self.assertIsInstance(d["expiry"], str)
        self.assertEqual(d["option_type"], "CALL")
        self.assertEqual(d["strike"], 100.0)

    def test_none_expiry_preserved(self):
        q = oa.OptionQuote(option_type="PUT", strike=95.0, expiry=None)
        self.assertIsNone(snap._serialize_quote(q)["expiry"])


class TestWriteSnapshot(unittest.TestCase):
    def setUp(self):
        self._orig = snap.OUT_DIR
        self._tmp = Path(tempfile.mkdtemp())
        snap.OUT_DIR = self._tmp                          # redirect writes off the real Data dir

    def tearDown(self):
        snap.OUT_DIR = self._orig
        for f in self._tmp.glob("*"):
            f.unlink()
        self._tmp.rmdir()

    def test_record_shape(self):
        q = oa.OptionQuote(option_type="PUT", strike=95.0, bid=2.0, ask=2.2, last=2.1,
                           expiry=datetime.date(2026, 10, 16))
        out = snap._write_snapshot("intc", spot=100.0, expiry=datetime.date(2026, 10, 16),
                                   quotes=[q], source="unit-test")
        self.assertEqual(Path(out).name, "INTC.jsonl")   # symbol upper-cased in the filename
        rec = json.loads(Path(out).read_text(encoding="utf-8").strip())
        self.assertEqual(rec["symbol"], "INTC")
        self.assertEqual(rec["source"], "unit-test")
        self.assertEqual(rec["spot"], 100.0)
        self.assertEqual(rec["expiry"], "2026-10-16")
        self.assertEqual(rec["n_quotes"], 1)
        self.assertEqual(len(rec["quotes"]), 1)
        self.assertEqual(rec["quotes"][0]["strike"], 95.0)
        self.assertIn("captured_at", rec)

    def test_append_is_additive(self):
        q = oa.OptionQuote(option_type="CALL", strike=100.0, expiry=datetime.date(2026, 10, 16))
        snap._write_snapshot("INTC", spot=100.0, expiry=datetime.date(2026, 10, 16),
                             quotes=[q], source="a")
        out = snap._write_snapshot("INTC", spot=101.0, expiry=datetime.date(2026, 10, 16),
                                   quotes=[q], source="b")
        lines = Path(out).read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)                  # JSONL append, not overwrite
        self.assertEqual(json.loads(lines[0])["source"], "a")
        self.assertEqual(json.loads(lines[1])["source"], "b")

    def test_dry_run_appends_from_fixture(self):
        rc = snap._dry_run("INTC")
        self.assertEqual(rc, 0)
        out = snap.OUT_DIR / "INTC.jsonl"
        self.assertTrue(out.exists())
        rec = json.loads(out.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(rec["source"], "dry-run-fixture")
        self.assertGreater(rec["n_quotes"], 0)           # fixture parsed to >=1 quote


ET = pytz.timezone("America/New_York")


class TestSymbols(unittest.TestCase):
    def test_written_call_symbols(self):
        state = {"positions": {"NVDA": {"written_call": {"strike": 1}}, "AMD": {"written_call": {"qty": 1}},
                               "MSFT": {"qty": 3}, "XLF": {"written_call": None}}}
        self.assertEqual(snap.written_call_symbols(state), ["AMD", "NVDA"])
        self.assertEqual(snap.written_call_symbols({}), [])

    def test_default_symbols_intc_first_then_covered_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "ai_portfolio_game.json"), "w", encoding="utf-8") as f:
                json.dump({"positions": {"NVDA": {"written_call": {"strike": 1}},
                                         "INTC": {"written_call": {"strike": 2}},
                                         "AMD": {"written_call": {"strike": 3}}}}, f)
            with mock.patch.dict(os.environ, {"AETHER_DATA_DIR": tmp}):
                self.assertEqual(snap.default_symbols(), ["INTC", "AMD", "NVDA"])

    def test_snapshots_go_to_the_same_data_dir_the_state_is_read_from(self):
        # AETHER_DATA_DIR moves both: symbols are read from it and snapshots appended under it.
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"AETHER_DATA_DIR": tmp}), \
                mock.patch.object(snap, "OUT_DIR", None):
            out = snap._write_snapshot("INTC", spot=1.0, expiry=None, quotes=[], source="test")
            self.assertEqual(out, Path(tmp) / "option_chains" / "INTC.jsonl")
            self.assertTrue(out.exists())

    def test_default_symbols_unreadable_state_is_intc_only(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"AETHER_DATA_DIR": tmp}):
            self.assertEqual(snap.default_symbols(), ["INTC"])


class TestTradingDay(unittest.TestCase):
    def test_weekday_weekend_holiday(self):
        self.assertTrue(snap.is_trading_day_et(ET.localize(datetime.datetime(2026, 10, 20, 12))))
        self.assertFalse(snap.is_trading_day_et(ET.localize(datetime.datetime(2026, 10, 24, 12))))   # Sat
        self.assertFalse(snap.is_trading_day_et(ET.localize(datetime.datetime(2026, 11, 26, 12))))   # Thanksgiving

    def test_uses_the_et_date(self):
        # Friday 22:30 PT is already Saturday in New York.
        pt = pytz.timezone("America/Los_Angeles").localize(datetime.datetime(2026, 10, 23, 22, 30))
        self.assertFalse(snap.is_trading_day_et(pt))


class TestMain(unittest.TestCase):
    """main() with snapshot() stubbed: no broker client, no network."""

    def _main(self, argv, trading_day=True, fails=()):
        calls = []

        def fake_snapshot(sym):
            calls.append(sym)
            return 2 if sym in fails else 0

        with (mock.patch.object(snap, "snapshot", side_effect=fake_snapshot),
              mock.patch.object(snap, "is_trading_day_et", return_value=trading_day),
              mock.patch.object(snap, "default_symbols", return_value=["INTC", "AMD", "NVDA"])):
            return snap.main(argv), calls

    def test_closed_day_takes_no_snapshot(self):
        self.assertEqual(self._main([], trading_day=False), (0, []))

    def test_force_snapshots_on_a_closed_day(self):
        self.assertEqual(self._main(["--force"], trading_day=False), (0, ["INTC", "AMD", "NVDA"]))

    def test_one_failure_still_tries_the_rest_and_exits_nonzero(self):
        self.assertEqual(self._main([], fails=("AMD",)), (2, ["INTC", "AMD", "NVDA"]))

    def test_symbol_flag_snapshots_one_symbol(self):
        self.assertEqual(self._main(["--symbol", "msft"]), (0, ["MSFT"]))


if __name__ == "__main__":
    unittest.main()
