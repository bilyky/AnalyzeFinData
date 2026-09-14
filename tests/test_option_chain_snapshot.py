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


if __name__ == "__main__":
    unittest.main()
