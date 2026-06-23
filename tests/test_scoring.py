"""
Unit tests for scoring.py pure functions.
No API calls, no external dependencies — runs with python -m unittest.

  python -m unittest discover -s tests -v
  # or with pytest when available:
  python -m pytest tests/ -v
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
import unittest.mock
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scoring
from scoring import (
    ohlcv_streak_perc, ohlcv_streak_count,
    week_of_month, predicted_win_pct,
    rel_volume_bucket, short_score, long_score,
    market_regime, clear_regime_cache,
    fibonacci_retracement_score,
    rsi_divergence_score,
)
from utils import _to_float
from tests.conftest import make_ohlcv


# ── _to_float ─────────────────────────────────────────────────────────────────

class TestToFloat(unittest.TestCase):
    def test_string_number(self):     self.assertEqual(_to_float("3.14", 0), 3.14)
    def test_none_default(self):      self.assertEqual(_to_float(None, -1), -1)
    def test_invalid_default(self):   self.assertEqual(_to_float("bad", 0.0), 0.0)
    def test_int_converts(self):      self.assertEqual(_to_float(42, 0), 42.0)
    def test_zero_string(self):       self.assertEqual(_to_float("0", 99), 0.0)
    def test_negative(self):          self.assertAlmostEqual(_to_float("-5.5", 0), -5.5)


# ── week_of_month ─────────────────────────────────────────────────────────────

class TestWeekOfMonth(unittest.TestCase):
    def test_boundaries(self):
        cases = [(1,1),(7,1),(8,2),(15,2),(16,3),(22,3),(23,4),(31,4)]
        for day, expected in cases:
            with self.subTest(day=day):
                self.assertEqual(week_of_month(day), expected)


# ── predicted_win_pct ─────────────────────────────────────────────────────────

class TestPredictedWinPct(unittest.TestCase):
    def test_all_buckets(self):
        self.assertAlmostEqual(predicted_win_pct(4.0),  0.643)
        self.assertAlmostEqual(predicted_win_pct(10.0), 0.643)
        self.assertAlmostEqual(predicted_win_pct(2.0),  0.576)
        self.assertAlmostEqual(predicted_win_pct(3.9),  0.576)
        self.assertAlmostEqual(predicted_win_pct(0.0),  0.531)
        self.assertAlmostEqual(predicted_win_pct(1.9),  0.531)
        self.assertAlmostEqual(predicted_win_pct(-0.1), 0.503)
        self.assertAlmostEqual(predicted_win_pct(-2.0), 0.503)
        self.assertAlmostEqual(predicted_win_pct(-2.1), 0.463)
        self.assertAlmostEqual(predicted_win_pct(-10.0),0.463)


# ── ohlcv_streak_perc ────────────────────────────────────────────────────────

class TestOhlcvStreakPerc(unittest.TestCase):
    def test_accumulates_up_days(self):
        ohlcv = make_ohlcv([100, 101, 103, 106])
        dates = sorted(ohlcv.keys())
        cur_pct = (106 - 103) / 103 * 100
        self.assertGreater(ohlcv_streak_perc(ohlcv, dates, 3, cur_pct), cur_pct)

    def test_zero_pct_returns_zero(self):
        ohlcv = make_ohlcv([100, 100])
        dates = sorted(ohlcv.keys())
        self.assertEqual(ohlcv_streak_perc(ohlcv, dates, 1, 0), 0)

    def test_direction_break_resets(self):
        # up, up, DOWN, up — streak accumulates cur day twice then breaks on DOWN
        ohlcv = make_ohlcv([100, 102, 104, 101, 103])
        dates = sorted(ohlcv.keys())
        cur_pct = (103 - 101) / 101 * 100
        result = ohlcv_streak_perc(ohlcv, dates, 4, cur_pct)
        # loop adds cur_pct again at i=idx-1 before hitting the DOWN break
        self.assertAlmostEqual(result, cur_pct * 2, places=2)

    def test_idx_zero_returns_cur_pct(self):
        ohlcv = make_ohlcv([100, 105])
        dates = sorted(ohlcv.keys())
        self.assertAlmostEqual(ohlcv_streak_perc(ohlcv, dates, 0, 5.0), 5.0, places=3)


# ── ohlcv_streak_count ───────────────────────────────────────────────────────

class TestOhlcvStreakCount(unittest.TestCase):
    def test_up_streak_positive(self):
        ohlcv = make_ohlcv([100, 101, 102, 103, 104])
        dates = sorted(ohlcv.keys())
        self.assertEqual(ohlcv_streak_count(ohlcv, dates, 4, 1.0), 5)

    def test_down_streak_negative(self):
        ohlcv = make_ohlcv([100, 99, 98, 97])
        dates = sorted(ohlcv.keys())
        result = ohlcv_streak_count(ohlcv, dates, 3, -1.0)
        self.assertLess(result, 0)

    def test_zero_pct_returns_zero(self):
        ohlcv = make_ohlcv([100, 100])
        dates = sorted(ohlcv.keys())
        self.assertEqual(ohlcv_streak_count(ohlcv, dates, 1, 0), 0)

    def test_idx_zero_returns_zero(self):
        ohlcv = make_ohlcv([100, 101])
        dates = sorted(ohlcv.keys())
        self.assertEqual(ohlcv_streak_count(ohlcv, dates, 0, 1.0), 0)


# ── rel_volume_bucket ────────────────────────────────────────────────────────

class TestRelVolumeBucket(unittest.TestCase):
    def _make(self, mult: float):
        base = 1_000_000
        vols = [base] * 20 + [int(base * mult)]
        ohlcv = make_ohlcv([100.0] * 21, volumes=vols)
        return ohlcv, sorted(ohlcv.keys())[-1]

    def test_all_buckets(self):
        cases = [
            (2.5, "Very High"),
            (2.0, "Very High"),  # boundary
            (1.7, "High"),
            (1.5, "High"),       # boundary
            (1.0, "Normal"),
            (0.5, "Low"),
        ]
        for mult, expected in cases:
            with self.subTest(mult=mult):
                self.assertEqual(rel_volume_bucket(*self._make(mult)), expected)

    def test_insufficient_data_returns_none(self):
        ohlcv = make_ohlcv([100.0] * 5)
        self.assertIsNone(rel_volume_bucket(ohlcv, sorted(ohlcv.keys())[-1]))

    def test_empty_returns_none(self):
        self.assertIsNone(rel_volume_bucket({}, "2024-01-10"))


# ── short_score ──────────────────────────────────────────────────────────────

class TestShortScore(unittest.TestCase):
    def test_all_positive_capped_at_10(self):
        fields = {"rel_vol": "High", "ob_os": "Optimal", "money_flow": "Strong",
                  "industry_strength": "Weak", "lt_trend": "Weak",
                  "seasonality": 1.0, "market_regime": "Bull"}
        # 2.5+3.0+3.0+2.0+1.5+1.0+1.0 = 14.0 → capped
        self.assertEqual(short_score(fields), 10.0)

    def test_all_negative_capped_at_minus_10(self):
        fields = {"rel_vol": "Low", "ob_os": "Wait", "money_flow": "Weak",
                  "industry_strength": "Strong", "lt_trend": "Strong",
                  "seasonality": -1.0, "market_regime": "Bear"}
        self.assertEqual(short_score(fields), -10.0)

    def test_neutral_is_zero(self):
        fields = {"rel_vol": "Normal", "ob_os": "Neutral", "money_flow": "Neutral",
                  "industry_strength": "", "lt_trend": "Neutral",
                  "seasonality": 0.0, "market_regime": "Neutral"}
        self.assertEqual(short_score(fields), 0.0)

    def test_empty_dict_is_zero(self):
        self.assertEqual(short_score({}), 0.0)

    # ── Regression guards: changing factor weights breaks these ─────────────

    def test_regression_exact_weights(self):
        # High(+2.5), Early(+1.0), Strong MF(+3.0), Weak ind(+2.0),
        # Neutral LT(0), season=0, Neutral regime = 8.5
        fields = {"rel_vol": "High", "ob_os": "Early", "money_flow": "Strong",
                  "industry_strength": "Weak", "lt_trend": "Neutral",
                  "seasonality": 0.0, "market_regime": "Neutral"}
        self.assertEqual(short_score(fields), 8.5)

    def test_very_high_vol_is_dampened(self):
        # Very High = +0.5, not +2.5 (news spike effect)
        fields = {"rel_vol": "Very High", "ob_os": "Neutral", "money_flow": "Neutral",
                  "industry_strength": "", "lt_trend": "Neutral",
                  "seasonality": 0.0, "market_regime": "Neutral"}
        self.assertEqual(short_score(fields), 0.5)

    def test_contrarian_industry_strength(self):
        # Weak industry = +2.0 in short (contrarian — oversold sector)
        fields = {"rel_vol": "Normal", "ob_os": "Neutral", "money_flow": "Neutral",
                  "industry_strength": "Weak", "lt_trend": "Neutral",
                  "seasonality": 0.0, "market_regime": "Neutral"}
        self.assertEqual(short_score(fields), 2.0)


# ── long_score ───────────────────────────────────────────────────────────────

class TestLongScore(unittest.TestCase):
    def test_all_positive_capped_at_10(self):
        fields = {"lt_trend": "Weak", "rel_vol": "High", "money_flow": "Strong",
                  "industry_strength": "Weak", "ob_os": "Optimal",
                  "seasonality": 1.0, "market_regime": "Bull"}
        self.assertEqual(long_score(fields), 10.0)

    def test_neutral_is_zero(self):
        fields = {"lt_trend": "Neutral", "rel_vol": "Normal", "money_flow": "Neutral",
                  "industry_strength": "", "ob_os": "Neutral",
                  "seasonality": 0.0, "market_regime": "Neutral"}
        self.assertEqual(long_score(fields), 0.0)

    def test_empty_dict_is_zero(self):
        self.assertEqual(long_score({}), 0.0)

    # ── Regression guards ───────────────────────────────────────────────────

    def test_regression_exact_weights(self):
        # Neutral LT(0), High(+2.0), Strong MF(+2.5), Weak ind(+2.0),
        # Early ob/os(+0.5), season 0.5×0.5(+0.25), Bull(+1.5) = 8.75 → round(,1) = 8.8
        fields = {"lt_trend": "Neutral", "rel_vol": "High", "money_flow": "Strong",
                  "industry_strength": "Weak", "ob_os": "Early",
                  "seasonality": 0.5, "market_regime": "Bull"}
        self.assertEqual(long_score(fields), 8.8)

    def test_seasonality_scaled_by_half(self):
        base = {"lt_trend": "Neutral", "rel_vol": "Normal", "money_flow": "Neutral",
                "industry_strength": "", "ob_os": "Neutral", "market_regime": "Neutral"}
        diff = long_score({**base, "seasonality": 1.0}) - long_score({**base, "seasonality": 0.0})
        self.assertAlmostEqual(diff, 0.5)

    def test_lt_trend_weak_is_contrarian_bull(self):
        # Weak LT Trend = +4.0 in long (oversold recovery play)
        fields = {"lt_trend": "Weak", "rel_vol": "Normal", "money_flow": "Neutral",
                  "industry_strength": "", "ob_os": "Neutral",
                  "seasonality": 0.0, "market_regime": "Neutral"}
        self.assertEqual(long_score(fields), 4.0)

    def test_regime_weight_1_5(self):
        # Bull regime = +1.5 in long (vs +1.0 in short)
        base = {"lt_trend": "Neutral", "rel_vol": "Normal", "money_flow": "Neutral",
                "industry_strength": "", "ob_os": "Neutral", "seasonality": 0.0}
        bull = long_score({**base, "market_regime": "Bull"})
        bear = long_score({**base, "market_regime": "Bear"})
        self.assertAlmostEqual(bull - bear, 3.0)  # +1.5 - (-1.5) = 3.0


# ── market_regime ─────────────────────────────────────────────────────────────

class TestMarketRegime(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._patches = []
        clear_regime_cache()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        clear_regime_cache()

    def _start_patch(self, target, new):
        p = unittest.mock.patch(target, new)
        p.start()
        self._patches.append(p)

    def _write_file(self, closes: list, symbol: str = "_TEST") -> str:
        """Write fake daily JSON, return last date string."""
        ts = {}
        d = date(2024, 1, 2)
        for c in closes:
            ts[str(d)] = {"4. close": str(c)}
            d += timedelta(days=1)
        sym_dir = os.path.join(self.tmpdir, "Data", "Symbol_full")
        os.makedirs(sym_dir, exist_ok=True)
        path = os.path.join(sym_dir, f"{symbol}_daily.json")
        with open(path, "w") as f:
            json.dump({"Time Series (Daily)": ts}, f)
        return str(d - timedelta(days=1))

    def _redirect(self, symbol: str = "_TEST"):
        """Patch abspath so scoring.py resolves files to self.tmpdir."""
        self._start_patch("scoring.os.path.abspath",
                          lambda _: os.path.join(self.tmpdir, "scoring.py"))
        self._start_patch("scoring.REGIME_SYMBOL", symbol)

    def test_bull(self):
        last = self._write_file([100.0] * 49 + [110.0])
        self._redirect()
        self.assertEqual(market_regime(last), "Bull")

    def test_bear(self):
        last = self._write_file([100.0] * 49 + [90.0])
        self._redirect()
        self.assertEqual(market_regime(last), "Bear")

    def test_neutral_flat(self):
        last = self._write_file([100.0] * 50)
        self._redirect()
        self.assertEqual(market_regime(last), "Neutral")

    def test_missing_file_is_neutral(self):
        os.makedirs(os.path.join(self.tmpdir, "Data", "Symbol_full"), exist_ok=True)
        self._redirect("_NOFILE")
        self.assertEqual(market_regime("2024-06-01"), "Neutral")

    def test_insufficient_history_is_neutral(self):
        last = self._write_file([100.0] * 5)
        self._redirect()
        self.assertEqual(market_regime(last), "Neutral")

    def test_disabled_when_symbol_empty(self):
        self._start_patch("scoring.REGIME_SYMBOL", "")
        self.assertEqual(market_regime("2024-06-01"), "Neutral")

    def test_cache_keyed_by_symbol(self):
        """Changing REGIME_SYMBOL must NOT return a cached result for the old symbol."""
        last_a = self._write_file([100.0] * 49 + [110.0], symbol="_A")
        self._write_file([100.0] * 49 + [90.0],  symbol="_B")
        self._start_patch("scoring.os.path.abspath",
                          lambda _: os.path.join(self.tmpdir, "scoring.py"))
        with unittest.mock.patch("scoring.REGIME_SYMBOL", "_A"):
            result_a = market_regime(last_a)
        with unittest.mock.patch("scoring.REGIME_SYMBOL", "_B"):
            result_b = market_regime(last_a)
        self.assertEqual(result_a, "Bull")
        self.assertEqual(result_b, "Bear")


# ── fibonacci_retracement_score ──────────────────────────────────────────────

class TestFibonacciRetracementScore(unittest.TestCase):
    def test_golden_support(self):
        # High 200, Low 100. Diff 100. 61.8% = 138.2
        # Price 138.5 (above level -> support)
        ohlcv = make_ohlcv([150]*20 + [100, 200, 138.5])
        dates = sorted(ohlcv.keys())
        score = fibonacci_retracement_score(ohlcv, dates[-1])
        self.assertEqual(score, 0.5)

    def test_golden_resistance(self):
        # Price 137.9 (below level -> resistance)
        ohlcv = make_ohlcv([150]*20 + [100, 200, 137.9])
        dates = sorted(ohlcv.keys())
        score = fibonacci_retracement_score(ohlcv, dates[-1])
        self.assertEqual(score, -0.5)

    def test_other_support(self):
        # High 200, Low 100. Diff 100.
        # 23.6% = 200 - 23.6 = 176.4
        # Price 177.5 (clearly above 176.4 -> support)
        ohlcv = make_ohlcv([150]*20 + [100, 200, 177.5])
        dates = sorted(ohlcv.keys())
        score = fibonacci_retracement_score(ohlcv, dates[-1])
        self.assertEqual(score, 0.25)

    def test_threshold_enforced(self):
        # 61.8% = 138.2. Price 140 (1.3% away -> should be 0)
        ohlcv = make_ohlcv([150]*20 + [100, 200, 140.0])
        dates = sorted(ohlcv.keys())
        score = fibonacci_retracement_score(ohlcv, dates[-1])
        self.assertEqual(score, 0.0)

    def test_insufficient_history(self):
        ohlcv = make_ohlcv([100, 110])
        dates = sorted(ohlcv.keys())
        self.assertEqual(fibonacci_retracement_score(ohlcv, dates[-1]), 0.0)


# ── rsi_divergence_score ─────────────────────────────────────────────────────

class TestRSIDivergenceScore(unittest.TestCase):
    RSI_PERIOD = 14
    SCAN_BARS  = 60

    def _make_div(self, price_pattern: list) -> dict:
        """Prepend enough flat warmup bars so total >= rsi_period + scan_bars + 5."""
        needed = self.RSI_PERIOD + self.SCAN_BARS + 5
        warmup = [100.0] * max(self.RSI_PERIOD + 5, needed - len(price_pattern))
        return make_ohlcv(warmup + price_pattern)

    def test_empty_ohlcv(self):
        self.assertEqual(rsi_divergence_score({}, "2024-06-01"), 0.0)

    def test_insufficient_history(self):
        ohlcv = make_ohlcv([100.0] * 18)
        self.assertEqual(rsi_divergence_score(ohlcv, sorted(ohlcv.keys())[-1]), 0.0)

    def test_no_divergence_returns_zero(self):
        # Flat data: every bar is both local min and max, but price diffs are 0
        # → neither bullish nor bearish condition can be satisfied
        ohlcv = make_ohlcv([100.0] * 90)
        self.assertEqual(rsi_divergence_score(ohlcv, sorted(ohlcv.keys())[-1]), 0.0)

    def test_bullish_divergence_strong(self):
        # Sharp drop to 85 (low1) → RSI ~0 (all losses from flat).
        # Slow grind to 82 (low2 < low1) → RSI well above 0 (less momentum).
        # Pattern is exactly 60 bars so it fills the scan window.
        flat  = [100.0] * 20
        drop1 = [100.0, 97.0, 94.0, 91.0, 88.0, 85.0, 85.0, 85.0]   # 8 bars, low at k=25..27
        recov = [86.0, 88.0, 90.0, 92.0, 93.0, 94.0, 93.0, 92.0, 91.0, 90.0]  # 10 bars
        drop2 = [90.0, 89.5, 89.0, 88.0, 86.0, 84.0, 82.0, 82.0, 82.0]        # 9 bars, low at k=44..46
        tail  = [83.0, 85.0, 87.0, 89.0, 91.0, 93.0, 94.0, 95.0, 96.0, 97.0, 98.0, 99.0, 100.0]  # 13 bars
        ohlcv = self._make_div(flat + drop1 + recov + drop2 + tail)
        result = rsi_divergence_score(ohlcv, sorted(ohlcv.keys())[-1])
        self.assertIn(result, [0.5, 1.0])

    def test_bearish_divergence_strong(self):
        # Sharp rise to 115 (high1) → RSI ~100.
        # Slow grind to 118 (high2 > high1) → RSI lower than high1 peak.
        flat    = [100.0] * 20
        rise1   = [100.0, 103.0, 106.0, 109.0, 112.0, 115.0, 115.0, 115.0]   # 8 bars, high k=25..27
        retrace = [114.0, 112.0, 110.0, 108.0, 107.0, 108.0, 109.0, 110.0, 111.0, 112.0]  # 10 bars
        rise2   = [112.0, 113.0, 114.0, 115.0, 116.0, 117.0, 118.0, 118.0, 118.0]          # 9 bars, high k=44..46
        tail    = [117.0, 115.0, 113.0, 111.0, 109.0, 107.0, 105.0, 103.0, 101.0, 100.0, 99.0, 98.0, 97.0]  # 13 bars
        ohlcv = self._make_div(flat + rise1 + retrace + rise2 + tail)
        result = rsi_divergence_score(ohlcv, sorted(ohlcv.keys())[-1])
        self.assertIn(result, [-0.5, -1.0])

    def test_bullish_mild_not_bearish(self):
        # Mild decline — result should be non-negative (no bearish signal).
        flat  = [100.0] * 20
        drop1 = [100.0, 99.5, 99.0, 98.8, 98.7, 98.7, 98.7]
        recov = [98.9, 99.2, 99.5, 99.7, 99.8, 99.7, 99.6, 99.5]
        drop2 = [99.5, 99.3, 99.1, 98.9, 98.5, 98.4, 98.3]
        tail  = [98.4, 98.5, 98.7, 98.9, 99.1, 99.3, 99.5, 99.7, 99.8, 99.9, 100.0, 100.0, 100.0]
        ohlcv = self._make_div(flat + drop1 + recov + drop2 + tail)
        result = rsi_divergence_score(ohlcv, sorted(ohlcv.keys())[-1])
        self.assertGreaterEqual(result, 0.0)   # never negative for a bullish setup


if __name__ == "__main__":
    unittest.main()
