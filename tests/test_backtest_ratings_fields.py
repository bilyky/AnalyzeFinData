"""backtest_ratings.compute_br builds the score fields production builds
(powergauge._compute_pgr_fields): setup_ok, vrecovery_score and digit_sum included."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import backtest_ratings as br


class TestReplayPassesSetup(unittest.TestCase):
    """backtest_ratings.compute_br must give short_score the same setup_ok production gives it
    (powergauge._compute_pgr_fields), or the R&D #34 breakout waiver never fires in replays."""

    def test_short_score_sees_setup_ok(self):
        closes = [100.0] * 25 + [110.0] * 5          # last bar above SMA20 and above 3 bars ago
        dates = [f"2026-02-{i + 1:02d}" for i in range(len(closes))]
        ts = {d: {"1. open": str(c), "2. high": str(c), "3. low": str(c), "4. close": str(c),
                  "5. volume": "1000"} for d, c in zip(dates, closes)}
        data = {"checklist_stocks": {"ltTrend": "Strong", "moneyFlow": "Strong", "industry": "Strong",
                                     "overboughtOversold": "Neutral", "pgrRating": 4}}
        seen = {}
        real = br._short_score_fn

        def spy(fields):
            seen.update(fields)
            return real(fields)

        with mock.patch.object(br, "_short_score_fn", side_effect=spy):
            br.compute_br(data, data, 112.0, len(dates) - 1, dates, ts, {})
        self.assertIn("setup_ok", seen)
        self.assertIs(seen["setup_ok"], True)

    def test_replay_passes_vrecovery_and_digit_sum_like_production(self):
        closes = [100.0] * 25 + [110.0] * 4 + [115.0]   # prior close 110 != snapshot-day close 115
        dates = [f"2026-02-{i + 1:02d}" for i in range(len(closes))]
        ts = {d: {"1. open": str(c), "2. high": str(c), "3. low": str(c), "4. close": str(c),
                  "5. volume": "1000"} for d, c in zip(dates, closes)}
        data = {"metaInfo": [{"symbol": "ABC"}], "checklist_stocks": {"ltTrend": "Strong"}}
        seen = {}
        with mock.patch.object(br, "_short_score_fn", side_effect=lambda f: seen.update(f) or 0.0),              mock.patch.object(br, "_rbr_score", return_value=(1.5, ["RBR"])) as rbr,              mock.patch.object(br, "_digit_sum_score", return_value=0.7) as ds:
            br.compute_br(data, data, 112.0, len(dates) - 1, dates, ts, {})
        self.assertEqual(seen["vrecovery_score"], 1.5)
        self.assertEqual(seen["digit_sum"], 0.7)
        rbr.assert_called_once_with(ts, dates[-1])
        # Production feeds the PRIOR close (powergauge._compute_pgr_fields), not the snapshot price.
        ds.assert_called_once_with("ABC", close_price=110.0)

    def test_callers_can_force_setup_ok(self):
        closes = [110.0] * 25 + [100.0] * 5          # last bar below SMA20 -> computed Setup is False
        dates = [f"2026-02-{i + 1:02d}" for i in range(len(closes))]
        ts = {d: {"1. open": str(c), "2. high": str(c), "3. low": str(c), "4. close": str(c),
                  "5. volume": "1000"} for d, c in zip(dates, closes)}
        data = {"checklist_stocks": {"ltTrend": "Strong"}}
        for forced, want in ((None, False), (True, True)):
            with mock.patch.object(br, "_short_score_fn", return_value=0.0) as spy:
                br.compute_br(data, data, 100.0, len(dates) - 1, dates, ts, {}, setup_ok=forced)
            self.assertIs(spy.call_args.args[0]["setup_ok"], want)


if __name__ == "__main__":
    unittest.main()
