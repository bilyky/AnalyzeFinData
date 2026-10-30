"""The replay's seasonality uses only 10-day outcomes known before the replayed date."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import backtest_ratings as br


def _series(year_returns):
    """Daily closes where the first March week of each year rises by the given % over 10 bars."""
    ts, price = {}, 100.0
    for year, pct in year_returns.items():
        days = [f"{year}-03-{d:02d}" for d in range(2, 31)]
        for k, d in enumerate(days):
            if 6 <= k <= 15:   # the 10 bars after week 1's last day (03-07), the slot's window
                price *= (1 + pct / 100) ** (1 / 10)
            ts[d] = {"4. close": f"{price:.6f}"}
    return ts


class TestSeasonalityAsOf(unittest.TestCase):
    def test_future_years_do_not_leak_into_an_earlier_date(self):
        # 2020-2022 March week 1: +3% each (tailwind). 2023-2026: -5% each.
        ts = _series({2020: 3, 2021: 3, 2022: 3, 2023: -5, 2024: -5, 2025: -5, 2026: -5})
        slots = br.precompute_seasonality(ts)
        self.assertEqual(slots.asof(3, 1, "2023-03-01"), 1.0)     # only 2020-2022 known
        self.assertEqual(slots.asof(3, 1, "2026-12-31"), -0.5)    # all 7 known: (9 - 20) / 7 = -1.57%

    def test_fewer_than_three_known_outcomes_score_zero(self):
        slots = br.precompute_seasonality(_series({2020: 3, 2021: 3, 2022: 3}))
        self.assertEqual(slots.asof(3, 1, "2022-01-01"), 0.0)     # only 2 known
        self.assertEqual(slots.asof(3, 1, "2023-01-01"), 1.0)

    def test_bucket_matches_production_thresholds(self):
        for avg, want in ((2.5, 1.0), (1.5, 0.5), (0.0, 0.0), (-1.5, -0.5), (-2.5, -1.0)):
            self.assertEqual(br._season_bucket(avg), want)


if __name__ == "__main__":
    unittest.main()
