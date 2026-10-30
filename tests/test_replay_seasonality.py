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

    def test_a_window_still_open_on_the_day_is_not_counted(self):
        """2023's window starts 03-07 and ends ten bars later; on 03-10 it is still open, so only
        2020-2022 count (#218 review: recording the start date as the outcome date leaked it)."""
        ts = _series({2020: 3, 2021: 3, 2022: 3, 2023: -6})
        slots = br.precompute_seasonality(ts)
        self.assertEqual(slots.asof(3, 1, "2023-03-10"), 1.0)      # 2023 would pull avg to +0.75 -> 0.0

    def test_a_window_counts_only_after_the_day_it_ends(self):
        ts = _series({2020: 3, 2021: 3, 2022: 3, 2023: -6})
        slots = br.precompute_seasonality(ts)
        end_2023 = [e for e in slots._slots[(3, 1)][0] if e.startswith("2023")][0]
        self.assertEqual(slots.asof(3, 1, end_2023), 1.0)          # its outcome is not known before that day
        day_after = sorted(d for d in ts if d > end_2023)[0]
        self.assertEqual(slots.asof(3, 1, day_after), 0.0)         # now counted: (9 - 6) / 4 = +0.75 -> 0.0

    def test_fewer_than_three_known_outcomes_score_zero(self):
        slots = br.precompute_seasonality(_series({2020: 3, 2021: 3, 2022: 3}))
        self.assertEqual(slots.asof(3, 1, "2022-01-01"), 0.0)     # only 2 known
        self.assertEqual(slots.asof(3, 1, "2023-01-01"), 1.0)

    def test_bucket_matches_production_thresholds(self):
        for avg, want in ((2.5, 1.0), (1.5, 0.5), (0.0, 0.0), (-1.5, -0.5), (-2.5, -1.0)):
            self.assertEqual(br._season_bucket(avg), want)


if __name__ == "__main__":
    unittest.main()
