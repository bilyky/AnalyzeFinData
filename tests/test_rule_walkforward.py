"""Rule masks, winner runs, per-date returns, the walk-forward chooser and the verdict."""
import os
import sys
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
import rule_walkforward as wf


def _row(day="2024-01-02", sym="A", s10=3.0, s10w=3.0, l60=3.0, above=True, up3=True, rr=3.0,
         gain=10.0, leader=False, mf_weak=False, past60=0.0, f20=1.0, f60=1.0, setup_full=None):
    full = (above and up3) if setup_full is None else setup_full
    return (day, sym, s10, s10w, l60, above, up3, rr, gain, leader, mf_weak, past60, f20, f60, full)


class TestAdmitMask(unittest.TestCase):
    def test_each_gate(self):
        rows = [_row(),                                  # 0 passes everything
                _row(s10=1.0, s10w=4.0),                 # 1 below floor 2 unless waived scores
                _row(above=False),                       # 2 fails the SMA half of Setup
                _row(up3=False),                         # 3 fails the 3-day half
                _row(rr=1.5),                            # 4 R/R below 2
                _row(gain=3.0),                          # 5 target gain below 5%
                _row(s10=-5.0, s10w=-5.0, leader=True),             # 6 leader, fails floor
                _row(s10=-5.0, s10w=-5.0, past60=-25.0),          # 7 rebound, money flow ok
                _row(s10=-5.0, s10w=-5.0, past60=-25.0, mf_weak=True)]   # 8 rebound but money flow Weak
        a = wf.to_arrays(rows)
        cases = {
            (2.0, "full", 2.0, False, False, False): [0],
            (2.0, "full", 2.0, True, False, False): [0, 1],
            (2.0, "sma", 2.0, False, False, False): [0, 3],
            (2.0, "dir", 2.0, False, False, False): [0, 2],
            (2.0, "off", 2.0, False, False, False): [0, 2, 3],
            (2.0, "full", 0.0, False, False, False): [0, 4, 5],
            (2.0, "full", 2.0, False, True, False): [0, 6],
            (2.0, "full", 2.0, False, False, True): [0, 7],
        }
        for rule, want in cases.items():
            with self.subTest(rule=rule):
                self.assertEqual(list(np.where(wf.admit_mask(a, rule))[0]), want)

    def test_elite_breakout_waives_rr(self):
        a = wf.to_arrays([_row(s10=6.0, l60=4.0, rr=0.5)])   # combined 10 >= 8, s10 >= 2
        self.assertTrue(wf.admit_mask(a, (2.0, "full", 2.0, False, False, False))[0])


class TestRunsAndReturns(unittest.TestCase):
    def test_winner_starts_split_runs_by_gap(self):
        rows = [_row(day="2024-01-02", f60=50), _row(day="2024-01-03", f60=45),
                _row(day="2024-02-20", f60=60), _row(day="2024-01-02", sym="B", f60=10)]
        a = wf.to_arrays(rows)
        self.assertEqual(sorted(wf.winner_starts(a).tolist()), [0, 2])

    def test_per_date_return_is_cash_when_nothing_admitted(self):
        day_idx = np.array([0, 0, 1])
        ret, n = wf.per_date_return(day_idx, 3, np.array([2.0, 4.0, 9.0]), np.array([True, True, False]))
        self.assertEqual(ret.tolist(), [3.0, 0.0, 0.0])
        self.assertEqual(n.tolist(), [2, 0, 0])


class TestChoose(unittest.TestCase):
    def test_constraints_then_best_mean(self):
        ret = np.array([[5.0, 5.0], [9.0, 0.0], [3.0, 3.0], [8.0, 8.0]])
        cnt = np.array([[1, 1], [1, 0], [1, 1], [10, 10]])
        loss = np.array([[0, 0], [0, 0], [0, 0], [9, 9]])   # rule 3 loses too often
        # rule 1 admits on 1 of 2 days (50% >= 20%) with mean 4.5; rule 0 mean 5 -> rule 0
        self.assertEqual(wf.choose(np.array([0, 1]), ret, cnt, loss, base_loss_share=0.1), 0)
        self.assertIsNone(wf.choose(np.array([0, 1]), ret * 0, cnt * 0, loss, 0.1))


class TestWalkForwardWindow(unittest.TestCase):
    def test_train_window_excludes_the_month_and_unknown_outcomes(self):
        days = np.array(["2022-12-01", "2023-02-02", "2023-12-01", "2023-12-15", "2024-01-02", "2024-01-20"])
        got = days[wf.train_window(days, "2024-01")].tolist()
        # 2023-12-15 is within 30 days of 2024-01-01: its 20-day outcome was not known yet.
        self.assertEqual(got, ["2023-02-02", "2023-12-01"])
        self.assertNotIn("2024-01-02", got)
        self.assertNotIn("2022-12-01", got)          # more than 12 x 31 days back (window starts 2022-12-25)

    def test_choose_skips_rules_admitting_on_too_few_days(self):
        days = np.arange(10)
        ret = np.array([[50.0] + [0.0] * 9, [1.0] * 10])      # rule 0: one huge day, 10% of days
        cnt = np.array([[1] + [0] * 9, [1] * 10])
        loss = np.zeros((2, 10), dtype=int)
        self.assertEqual(wf.choose(days, ret, cnt, loss, base_loss_share=1.0), 1)

    def test_run_tests_the_pooled_difference_with_lag_19(self):
        rows = []
        for m in ("2023-01", "2023-02", "2023-03", "2023-11", "2023-12", "2024-01", "2024-02"):
            for d in range(2, 26):
                for k in range(6):
                    rows.append(_row(day=f"{m}-{d:02d}", sym=f"S{k}", s10=float(k), s10w=float(k), l60=4.0,
                                     f20=float(k - 2), f60=float(k)))
        lags = []
        real = wf.sg._t_hac
        with mock.patch.object(wf.sg, "_t_hac", side_effect=lambda xs, lag: lags.append(lag) or real(xs, lag)):
            wf.run(rows)
        self.assertTrue(lags)
        self.assertTrue(all(lag == 19 for lag in lags), lags)


class TestVerdict(unittest.TestCase):
    def test_table(self):
        self.assertEqual(wf.verdict(0.5, 2.5, 0.4, 0.1), "ADAPTIVE_HELPS")
        self.assertEqual(wf.verdict(0.5, 2.5, 0.05, 0.1), "INCONCLUSIVE")   # catches fewer winners
        self.assertEqual(wf.verdict(0.5, 1.0, 0.4, 0.1), "INCONCLUSIVE")
        self.assertEqual(wf.verdict(-0.5, -2.5, 0.4, 0.1), "ADAPTIVE_HURTS")
        self.assertEqual(wf.verdict(None, None, 0, 0), "INCONCLUSIVE")


if __name__ == "__main__":
    unittest.main()
