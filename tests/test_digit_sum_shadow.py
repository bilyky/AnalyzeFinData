"""Digit-sum shadow (owner 2026-11-24, R&D #72): the term counts only for stocks on the confirmed list."""
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import powergauge  # noqa: E402
from aether import scoring  # noqa: E402
from tests.test_compute import _make_pg_with_ohlcv  # noqa: E402


def _allowed(symbol, path=None):
    return symbol.upper().strip() in powergauge._digit_allowlist(path)


class TestAllowlist(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "allow.json")
        powergauge._digit_allow.update(key=None, symbols=frozenset())

    def tearDown(self):
        self.tmp.cleanup()
        powergauge._digit_allow.update(key=None, symbols=frozenset())

    def _write(self, payload, bump=0):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(payload if isinstance(payload, str) else json.dumps(payload))
        t = time.time() + bump
        os.utime(self.path, (t, t))

    def test_missing_list_means_no_stock(self):
        self.assertFalse(_allowed("AAPL", self.path))

    def test_listed_stock_only(self):
        self._write({"symbols": ["aapl", " MSFT "]})
        self.assertTrue(_allowed("AAPL", self.path))
        self.assertTrue(_allowed("msft", self.path))
        self.assertFalse(_allowed("NVDA", self.path))

    def test_reloads_when_the_list_changes(self):
        self._write({"symbols": ["AAPL"]})
        self.assertTrue(_allowed("AAPL", self.path))
        self._write({"symbols": []}, bump=5)
        self.assertFalse(_allowed("AAPL", self.path))

    def test_unreadable_list_means_no_stock(self):
        self._write("not json")
        self.assertFalse(_allowed("AAPL", self.path))
        self._write(["AAPL"], bump=5)                       # wrong shape
        self.assertFalse(_allowed("AAPL", self.path))

    def test_default_path_is_the_data_dir(self):
        with mock.patch.object(powergauge.paths, "data_dir", return_value=self.tmp.name):
            with open(os.path.join(self.tmp.name, powergauge.DIGIT_ALLOWLIST_NAME), "w") as f:
                json.dump({"symbols": ["TEST"]}, f)
            self.assertTrue(_allowed("TEST"))


class TestFieldsShadow(unittest.TestCase):
    def setUp(self):
        scoring._digit_index, scoring._digit_full_index = {}, {}
        scoring.clear_regime_cache()

    def tearDown(self):
        scoring._digit_index, scoring._digit_full_index = None, None
        scoring.clear_regime_cache()

    def _fields(self, allowed):
        pg, ohlcv = _make_pg_with_ohlcv()
        with mock.patch.object(powergauge, "_digit_sum_score", return_value=0.8), \
             mock.patch.object(powergauge, "_digit_allowlist",
                               return_value=frozenset({"TEST"}) if allowed else frozenset()):
            return powergauge._compute_pgr_fields(pg, ohlcv_ts=ohlcv)

    def test_shadowed_stock_keeps_the_raw_score_but_scores_ignore_it(self):
        off, on = self._fields(False), self._fields(True)
        self.assertEqual((off["digit_sum_raw"], off["digit_sum"]), (0.8, 0.0))
        self.assertEqual((on["digit_sum_raw"], on["digit_sum"]), (0.8, 0.8))
        # Short10 adds the term in full and rounds to 0.1, so the gap is 0.8 +/- 0.1
        self.assertAlmostEqual(on["short_score"] - off["short_score"], 0.8, delta=0.11)


if __name__ == "__main__":
    unittest.main()
