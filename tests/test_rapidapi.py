"""
Red-green tests for the OHLCV real-volume data-integrity fix.

Covers the bar-provenance model:
  - Chaikin close-only bars are marked ``provisional`` (powergauge._append_ohlcv_entry).
  - The recovery gate (rapidapi._check_recovery) repairs a provisional/volume==0 latest
    bar even when its date is current, and skips any bar with real volume.
  - _fetch_and_merge overwrites a provisional latest bar with the real API bar.

All HTTP is mocked; no network, no live RapidAPI key required.
"""
import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import bar_provenance
import rapidapi
import powergauge

TODAY = "2026-08-05"          # gap == 0 vs. a latest bar dated the same day
STALE = "2026-01-01"          # > MAX_GAP_DAYS behind TODAY


def _real_bar(o, h, l, c, v):
    return {"1. open": str(o), "2. high": str(h), "3. low": str(l),
            "4. close": str(c), "5. volume": str(v)}


def _cache(ts: dict, refreshed: str) -> dict:
    return {"Meta Data": {"3. Last Refreshed": refreshed}, "Time Series (Daily)": ts}


def _write(dirpath, sym, cache):
    path = os.path.join(dirpath, f"{sym}_daily.json")
    with open(path, "w") as f:
        json.dump(cache, f)
    return path


class TestProvenanceHelpers(unittest.TestCase):

    def test_is_provisional(self):
        # Explicit flag, and the volume==0 fallback for legacy bars written before it existed.
        self.assertTrue(bar_provenance.is_provisional({"4. close": "10", "5. volume": "12345",
                                                       "provisional": True}))
        self.assertTrue(bar_provenance.is_provisional(_real_bar(10, 10, 10, 10, 0)))
        # Real bar (volume>0, no flag) is not provisional; junk inputs fall back to False.
        self.assertFalse(bar_provenance.is_provisional(_real_bar(10, 11, 9, 10, 500000)))
        self.assertFalse(bar_provenance.is_provisional(None))
        self.assertFalse(bar_provenance.is_provisional({"5. volume": "not-a-number"}))

class TestCheckRecovery(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def test_provisional_latest_triggers_repair(self):
        ts = {"2026-08-04": _real_bar(10, 11, 9, 10, 400000),
              TODAY: {"1. open": "10", "2. high": "10", "3. low": "10",
                      "4. close": "10", "5. volume": "0", "provisional": True}}
        path = _write(self.dir, "AAA", _cache(ts, TODAY))
        needs, cache = rapidapi._check_recovery(path, TODAY)
        self.assertTrue(needs)                       # green after fix (red before)
        self.assertIsNotNone(cache)

    def test_zero_volume_latest_triggers_repair(self):
        ts = {"2026-08-04": _real_bar(10, 11, 9, 10, 400000),
              TODAY: _real_bar(10, 10, 10, 10, 0)}   # legacy degraded, no flag
        path = _write(self.dir, "BBB", _cache(ts, TODAY))
        needs, _ = rapidapi._check_recovery(path, TODAY)
        self.assertTrue(needs)

    def test_fresh_real_latest_is_current(self):
        ts = {"2026-08-04": _real_bar(10, 11, 9, 10, 400000),
              TODAY: _real_bar(10, 11, 9, 10, 500000)}  # legacy real, volume>0, no flag
        path = _write(self.dir, "DDD", _cache(ts, TODAY))
        needs, _ = rapidapi._check_recovery(path, TODAY)
        self.assertFalse(needs)

    def test_stale_real_latest_triggers_repair(self):
        ts = {STALE: _real_bar(10, 11, 9, 10, 500000)}  # gap > MAX_GAP_DAYS
        path = _write(self.dir, "EEE", _cache(ts, STALE))
        needs, _ = rapidapi._check_recovery(path, TODAY)
        self.assertTrue(needs)

    def test_missing_file_triggers_repair(self):
        needs, cache = rapidapi._check_recovery(
            os.path.join(self.dir, "NOPE_daily.json"), TODAY)
        self.assertTrue(needs)
        self.assertIsNone(cache)


class TestFetchAndMerge(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def test_overwrites_provisional_latest_with_real_bar(self):
        # Existing cache: real history + a provisional today bar (volume 0, low==close).
        ts = {"2026-08-03": _real_bar(10, 11, 9, 10, 300000),
              "2026-08-04": _real_bar(10, 12, 10, 11, 350000),
              TODAY: {"1. open": "11", "2. high": "11", "3. low": "11",
                      "4. close": "11", "5. volume": "0", "provisional": True}}
        path = _write(self.dir, "FFF", _cache(ts, TODAY))

        # RapidAPI returns the settled bar for today with real volume/range.
        raw = {"Meta Data": {"3. Last Refreshed": TODAY},
               "Time Series (Daily)": {TODAY: _real_bar(10.5, 12.3, 10.2, 11.8, 1250000)}}

        with mock.patch.object(rapidapi, "_fetch_raw", return_value=raw):
            rapidapi._fetch_and_merge("FFF", path, outputsize="compact")

        with open(path) as f:
            merged = json.load(f)["Time Series (Daily)"]
        bar = merged[TODAY]
        self.assertEqual(float(bar["5. volume"]), 1250000)   # real volume in
        self.assertNotIn("provisional", bar)                 # placeholder gone
        self.assertFalse(bar_provenance.is_provisional(bar))  # recognized as a real bar
        # A subsequent recovery check now skips this symbol.
        needs, _ = rapidapi._check_recovery(path, TODAY)
        self.assertFalse(needs)

    def test_full_fetch_writes_real_nonprovisional_bar(self):
        raw = {"Meta Data": {"3. Last Refreshed": TODAY},
               "Time Series (Daily)": {
                   "2026-08-04": _real_bar(10, 12, 10, 11, 350000),
                   TODAY: _real_bar(10.5, 12.3, 10.2, 11.8, 1250000)}}
        path = os.path.join(self.dir, "GGG_daily.json")  # file absent → full write
        with mock.patch.object(rapidapi, "_fetch_raw", return_value=raw):
            rapidapi._fetch_and_merge("GGG", path, outputsize="full")
        with open(path) as f:
            ts = json.load(f)["Time Series (Daily)"]
        self.assertFalse(bar_provenance.is_provisional(ts[TODAY]))

    def test_zero_volume_api_bar_stays_provisional(self):
        # Real API data whose latest bar has volume 0 (delisted/dead symbol's final print).
        # A zero-volume bar must still be treated as provisional so consumers skip it.
        # (Regression: caught on live HOLX/IRBT/K/PCH.)
        raw = {"Meta Data": {"3. Last Refreshed": TODAY},
               "Time Series (Daily)": {
                   "2026-08-03": _real_bar(10, 12, 10, 11, 350000),
                   TODAY: _real_bar(11, 11, 11, 11, 0)}}
        path = os.path.join(self.dir, "III_daily.json")
        with mock.patch.object(rapidapi, "_fetch_raw", return_value=raw):
            rapidapi._fetch_and_merge("III", path, outputsize="full")
        with open(path) as f:
            bar = json.load(f)["Time Series (Daily)"][TODAY]
        self.assertTrue(bar_provenance.is_provisional(bar))     # vol 0 → still a placeholder


class TestAppendOhlcvEntry(unittest.TestCase):
    """powergauge._append_ohlcv_entry must mark the synthetic bar provisional."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        self._orig_dir = powergauge.OHLCV_DIR
        powergauge.OHLCV_DIR = self.dir
        self.addCleanup(lambda: setattr(powergauge, "OHLCV_DIR", self._orig_dir))

    def test_appends_provisional_bar_with_correct_close(self):
        sym = "HHH"
        ohlcv_full = _cache({"2026-08-04": _real_bar(10, 12, 10, 11, 350000)}, "2026-08-04")
        # File must exist on disk (function guards on os.path.exists).
        _write(self.dir, sym, ohlcv_full)
        power_g = types.SimpleNamespace(price=11.55, max_price=11.90)

        powergauge._append_ohlcv_entry(sym, TODAY, power_g, ohlcv_full)

        with open(os.path.join(self.dir, f"{sym}_daily.json")) as f:
            bar = json.load(f)["Time Series (Daily)"][TODAY]
        self.assertEqual(float(bar["4. close"]), 11.55)   # real close preserved
        self.assertTrue(bar.get("provisional"))           # marked placeholder
        self.assertTrue(bar_provenance.is_provisional(bar))  # recognized by the gate


class TestUnsupportedSymbols(unittest.TestCase):
    """repair_missing skips only true index aliases — never a real universe equity."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        self._orig_dir = rapidapi.OHLCV_DIR
        rapidapi.OHLCV_DIR = self.dir
        self.addCleanup(lambda: setattr(rapidapi, "OHLCV_DIR", self._orig_dir))

    def test_index_alias_is_skipped_without_fetch(self):
        # A true index pseudo-ticker AV's daily endpoint can't serve is skipped, no HTTP.
        with mock.patch.object(rapidapi, "_fetch_raw") as m:
            res = rapidapi.repair_missing(["SPX"], TODAY, force=True)
        m.assert_not_called()
        self.assertEqual(res["skipped"], 1)
        self.assertEqual(res["updated"], 0)

    def test_real_equity_ticker_is_not_excluded(self):
        # Regression: COMP is Compass, Inc. (NYSE), not the Nasdaq Composite. It must NOT be
        # hardcoded-excluded, or its stale cache would never get repaired.
        self.assertNotIn("COMP", rapidapi._UNSUPPORTED_SYMBOLS)
        raw = {"Meta Data": {"3. Last Refreshed": TODAY},
               "Time Series (Daily)": {TODAY: _real_bar(7.8, 8.0, 7.7, 7.85, 6208611)}}
        with mock.patch.object(rapidapi, "SLEEP_SEC", 0), \
             mock.patch.object(rapidapi, "_fetch_raw", return_value=raw) as m:
            res = rapidapi.repair_missing(["COMP"], TODAY, force=True)
        m.assert_called_once()                       # a fetch was actually attempted
        self.assertEqual(res["updated"], 1)


class TestRepairMissingRateLimiting(unittest.TestCase):
    """Verify that repair_missing sleeps even on failure to avoid rapid-fire cascades."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        self._orig_dir = rapidapi.OHLCV_DIR
        rapidapi.OHLCV_DIR = self.dir
        self.addCleanup(lambda: setattr(rapidapi, "OHLCV_DIR", self._orig_dir))

    def test_sleeps_on_failure_to_prevent_hammering_api(self):
        with mock.patch.object(rapidapi, "_fetch_and_merge", side_effect=RuntimeError("API Failure")), \
             mock.patch("rapidapi.time.sleep") as mock_sleep:
            res = rapidapi.repair_missing(["AAA", "BBB"], TODAY, force=True)
            
        self.assertEqual(res["updated"], 0)
        self.assertEqual(len(res["errors"]), 2)
        self.assertEqual(mock_sleep.call_count, 2)
        mock_sleep.assert_has_calls([mock.call(rapidapi.SLEEP_SEC), mock.call(rapidapi.SLEEP_SEC)])


if __name__ == "__main__":
    unittest.main()
