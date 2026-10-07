"""
Red-green tests for the stranded-placeholder fix.

Chaikin close-only placeholder bars (bar_provenance.is_provisional) used to survive inside
the OHLCV series — weekend prints the API never returns, and weekday ones outside the
merge's newest-3 window — and every range consumer read them as flat zero-volume bars,
shrinking ATR (live stops too tight). Pins:
  - the shared filter (real_dates / is_weekend);
  - all three loaders drop interior placeholders (ohlcv_to_array, calculate_atr,
    _load_ohlcv_series), so ATR on a placeholder-laden series == ATR on its real bars;
  - true range is gap-aware: after a hole left by dropped placeholders a bar counts only
    its own high-low, so a multi-day move never inflates ATR;
  - the writer never appends a weekend placeholder;
  - the merge settles every placeholder in the response window (replace, or drop a
    non-session) and _check_recovery fires on an interior one;
  - the one-off prune script: dry run writes nothing, --apply backs up first.
All I/O is in temp dirs; no network.
"""
import datetime
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "sync"))

import bar_provenance
import powergauge
import prune_weekend_placeholders as prune
import rapidapi
from aether import patterns, risk_utils


def _real(px, vol=1000, rng=2.0):
    return {"1. open": str(px), "2. high": str(px + rng / 2), "3. low": str(px - rng / 2),
            "4. close": str(px), "5. volume": str(vol)}


def _ph(px):
    return {"1. open": str(px), "2. high": str(px), "3. low": str(px), "4. close": str(px),
            "5. volume": "0", "provisional": True}


def _weekdays(start: str, n: int) -> list[str]:
    d, out = datetime.date.fromisoformat(start), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += datetime.timedelta(days=1)
    return out


DAYS = _weekdays("2026-06-01", 40)   # Mon 2026-06-01 ...


def _laden_series():
    """40 real weekday bars + a placeholder on every weekend + 5 weekday placeholders."""
    ts = {d: _real(100) for d in DAYS}
    for d in DAYS[5:30:5]:
        ts[d] = _ph(100)
    day = datetime.date.fromisoformat(DAYS[0])
    while day.isoformat() <= DAYS[-1]:
        if day.weekday() >= 5:
            ts[day.isoformat()] = _ph(100)
        day += datetime.timedelta(days=1)
    return ts


class TestProvenanceHelpers(unittest.TestCase):
    def test_real_dates_drops_every_placeholder(self):
        ts = {"2026-06-01": _real(1), "2026-06-02": _ph(1), "2026-06-03": _real(1)}
        self.assertEqual(bar_provenance.real_dates(ts), ["2026-06-01", "2026-06-03"])

    def test_is_weekend(self):
        self.assertTrue(bar_provenance.is_weekend("2026-09-27"))     # Sunday
        self.assertTrue(bar_provenance.is_weekend("2026-09-26"))     # Saturday
        self.assertFalse(bar_provenance.is_weekend("2026-09-25"))    # Friday


class _OhlcvDir(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name

    def write(self, sym, ts):
        path = os.path.join(self.dir, f"{sym}_daily.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"Meta Data": {}, "Time Series (Daily)": ts}, f)
        return path


class TestLoadersDropInteriorPlaceholders(_OhlcvDir):
    def test_ohlcv_to_array_drops_interior(self):
        ts = _laden_series()
        arr = patterns.ohlcv_to_array(ts, DAYS[-1], lookback=250)
        self.assertEqual(len(arr), len(DAYS) - 5)
        self.assertTrue((arr[:, 4] > 0).all())                     # no zero-volume rows

    def test_calculate_atr_ignores_placeholders(self):
        clean = {d: b for d, b in _laden_series().items() if not b.get("provisional")}
        self.write("LADEN", _laden_series())
        self.write("CLEAN", clean)
        with mock.patch.object(risk_utils, "OHLCV_DIR", Path(self.dir)):
            laden, real = risk_utils.calculate_atr("LADEN"), risk_utils.calculate_atr("CLEAN")
        self.assertIsNotNone(real)
        self.assertAlmostEqual(laden, real)

    def test_load_ohlcv_series_drops_placeholders(self):
        ts = _laden_series()
        ts["2026-07-27"] = _ph(100)                                  # trailing (Mon after DAYS[-1])
        self.write("LADEN", ts)
        with mock.patch.object(risk_utils, "OHLCV_DIR", Path(self.dir)):
            highs, lows, closes, last = risk_utils._load_ohlcv_series("LADEN")
        self.assertEqual(len(closes), len(DAYS) - 5)
        self.assertEqual(last, DAYS[-1])
        self.assertTrue(all(h > lo for h, lo in zip(highs, lows, strict=True)))


class TestGapAwareTrueRange(_OhlcvDir):
    """Dropping placeholders can leave real bars sessions apart; a true range measured
    against a weeks-old close is a multi-day move. Bars after a gap count high-low only."""

    @staticmethod
    def _gapped():
        # 20 real weekday bars at ~100 (range 2), a 3-week hole, then 20 bars at ~150.
        first = _weekdays("2026-06-01", 20)
        second = _weekdays("2026-07-20", 20)
        ts = {d: _real(100) for d in first}
        ts.update({d: _real(150) for d in second})
        return ts, first + second

    def test_session_gaps(self):
        flags = risk_utils.session_gaps(["2026-06-05", "2026-06-08", "2026-06-15", "2026-06-16"])
        self.assertEqual(flags, [False, False, True, False])     # Fri->Mon ok, 7-day hole flagged

    def test_atr_from_series_ignores_cross_gap_move(self):
        highs, lows, closes = [101, 151, 151], [99, 149, 149], [100, 150, 150]
        self.assertEqual(risk_utils._atr_from_series(highs, lows, closes, gaps=[False, True, False]), 2.0)
        self.assertGreater(risk_utils._atr_from_series(highs, lows, closes), 2.0)  # ungapped: inflated

    def test_calculate_atr_across_gap_stays_at_bar_range(self):
        ts, _ = self._gapped()
        self.write("GAP", ts)
        with mock.patch.object(risk_utils, "OHLCV_DIR", Path(self.dir)):
            atr = risk_utils.calculate_atr("GAP", period=30)          # window spans the gap
        self.assertAlmostEqual(atr, 2.0)

    def test_resolve_stop_atr_path_is_gap_aware(self):
        ts, days = self._gapped()
        self.write("GAP", ts)
        with mock.patch.object(risk_utils, "OHLCV_DIR", Path(self.dir)),              mock.patch.object(risk_utils, "_age_days", return_value=1):
            d = risk_utils.resolve_stop_detailed(150.0, symbol="GAP", exclude_swing=True)
        self.assertEqual(d["source"], "atr")
        self.assertAlmostEqual(d["stop"], 150.0 - 2.5 * 2.0)


class TestWriterSkipsWeekends(_OhlcvDir):
    def setUp(self):
        super().setUp()
        self._orig = powergauge.OHLCV_DIR
        powergauge.OHLCV_DIR = self.dir
        self.addCleanup(lambda: setattr(powergauge, "OHLCV_DIR", self._orig))

    def _append(self, day):
        full = {"Meta Data": {}, "Time Series (Daily)": {"2026-09-25": _real(10)}}
        path = self.write("SYM", full["Time Series (Daily)"])
        powergauge._append_ohlcv_entry("SYM", day, types.SimpleNamespace(price=11.0, max_price=11.5), full)
        with open(path, encoding="utf-8") as f:
            return json.load(f)["Time Series (Daily)"]

    def test_weekend_is_not_appended(self):
        self.assertNotIn("2026-09-27", self._append("2026-09-27"))   # Sunday

    def test_weekday_still_appended(self):
        self.assertTrue(self._append("2026-09-28")["2026-09-28"].get("provisional"))  # Monday


class TestMergeSettlesPlaceholders(_OhlcvDir):
    def setUp(self):
        super().setUp()
        self._orig = rapidapi.OHLCV_DIR
        rapidapi.OHLCV_DIR = self.dir
        self.addCleanup(lambda: setattr(rapidapi, "OHLCV_DIR", self._orig))

    def test_replaces_in_window_and_drops_non_sessions(self):
        # Existing: an old placeholder (before the response window), an interior weekday
        # placeholder the API covers, a weekend placeholder inside the window.
        existing = {d: _real(100) for d in DAYS}
        existing[DAYS[0]] = _ph(100)          # older than the response window -> kept
        existing[DAYS[20]] = _ph(100)         # API has it -> replaced
        existing["2026-06-27"] = _ph(100)     # Saturday inside the window -> dropped
        path = self.write("SYM", existing)
        api = {d: _real(101, vol=5000) for d in DAYS[10:]}
        with mock.patch.object(rapidapi, "_fetch_raw", return_value={"Time Series (Daily)": api}):
            rapidapi._fetch_and_merge("SYM", path, outputsize="compact")
        with open(path, encoding="utf-8") as f:
            ts = json.load(f)["Time Series (Daily)"]
        self.assertEqual(ts[DAYS[20]]["5. volume"], "5000")
        self.assertNotIn("2026-06-27", ts)
        self.assertTrue(ts[DAYS[0]].get("provisional"))

    def test_check_recovery_fires_on_interior_placeholder(self):
        ts = {d: _real(100) for d in DAYS}
        ts[DAYS[20]] = _ph(100)
        path = self.write("SYM", ts)
        needs, _ = rapidapi._check_recovery(path, DAYS[-1])
        self.assertTrue(needs)
        ts[DAYS[20]] = _real(100)
        self.write("SYM", ts)
        self.assertFalse(rapidapi._check_recovery(path, DAYS[-1])[0])


class TestPruneScript(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data = self._tmp.name
        os.makedirs(os.path.join(self.data, "Symbol_full"))
        self.path = os.path.join(self.data, "Symbol_full", "SYM_daily.json")
        ts = {"2026-09-25": _real(10), "2026-09-26": _ph(10), "2026-09-28": _ph(11)}
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"Meta Data": {}, "Time Series (Daily)": ts}, f)

    def _ts(self):
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)["Time Series (Daily)"]

    def test_dry_run_writes_nothing(self):
        with mock.patch.object(prune, "_out"):
            r = prune.run(self.data, apply=False)
        self.assertEqual((r["removed"], r["weekday_left"]), (1, 1))
        self.assertIn("2026-09-26", self._ts())
        self.assertFalse(os.path.exists(os.path.join(self.data, "Backup")))

    def test_apply_backs_up_then_removes_weekend_only(self):
        with mock.patch.object(prune, "_out"):
            r = prune.run(self.data, apply=True)
        ts = self._ts()
        self.assertNotIn("2026-09-26", ts)               # Saturday placeholder gone
        self.assertIn("2026-09-28", ts)                  # weekday placeholder kept for repair
        with open(os.path.join(r["backup_dir"], "SYM_daily.json"), encoding="utf-8") as f:
            self.assertIn("2026-09-26", json.load(f)["Time Series (Daily)"])


if __name__ == "__main__":
    unittest.main()
