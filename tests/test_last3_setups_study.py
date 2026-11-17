"""Tests for scripts/backtesting/last3_setups_study.py: event thinning (non-overlapping 20-session
windows), the prior record (only completed earlier events, None until 3 exist), the monthly
HIGH-vs-LOW test, and the verdict, with fixtures where the pooled and the per-month view differ."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
import last3_setups_study as ls  # noqa: E402


def ev(date, excess, prior=None, mf="Neutral", sym="X"):
    return {"symbol": sym, "date": date, "excess": excess, "prior": prior, "mf": mf}


class TestEvents(unittest.TestCase):
    def test_thin_keeps_events_20_sessions_apart_forward_in_time(self):
        cands = [(0, "a"), (5, "b"), (19, "c"), (20, "d"), (39, "e"), (41, "f")]
        self.assertEqual([c[1] for c in ls.thin(cands)], ["a", "d", "f"])

    def test_prior_counts_only_the_previous_three_and_starts_at_the_fourth(self):
        evts = [ev("2024-01-02", x) for x in (1.0, -1.0, 2.0, 3.0, -2.0)]
        p = [e["prior"] for e in ls.with_prior(evts)]
        self.assertEqual(p, [None, None, None, 2, 2])     # 4th sees (+,-,+); 5th sees (-,+,+)

    def test_prior_ignores_its_own_outcome(self):
        evts = [ev("d", -1.0), ev("d", -1.0), ev("d", -1.0), ev("d", 50.0)]
        self.assertEqual(ls.with_prior(evts)[3]["prior"], 0)

    def test_spy_return_uses_closes_on_or_before_the_day(self):
        spy = (["2024-01-02", "2024-01-03", "2024-01-04"], {"2024-01-02": 100.0, "2024-01-03": 101.0,
                                                             "2024-01-04": 110.0})
        self.assertAlmostEqual(ls.spy_return(spy, "2024-01-02", n=2), 10.0)
        self.assertAlmostEqual(ls.spy_return(spy, "2024-01-02T", n=1), 1.0)   # a non-trading day maps back
        self.assertIsNone(ls.spy_return(spy, "2024-01-03", n=2))


class TestStatistic(unittest.TestCase):
    def test_monthly_test_compares_within_months(self):
        # pooled, HIGH looks better (it sits in a good month); within each month it is worse by 1
        evts = []
        for m in range(1, 15):
            good = m % 2 == 0
            base = 10.0 if good else -10.0
            evts += [ev(f"2024-{m:02d}-05" if m <= 12 else f"2025-{m-12:02d}-05", base - 1, prior=3),
                     ev(f"2024-{m:02d}-06" if m <= 12 else f"2025-{m-12:02d}-06", base, prior=0)]
            if good:
                evts.append(ev(f"2024-{m:02d}-07" if m <= 12 else f"2025-{m-12:02d}-07", base - 1, prior=3))
        t = ls.monthly_test(evts, lambda e: e["prior"] >= 2, lambda e: e["prior"] <= 1, (-100, 100))
        self.assertEqual(t["months"], 14)
        self.assertAlmostEqual(t["mean_diff"], -1.0)

    def test_winsor_bounds_clip_outliers(self):
        evts = [ev(f"2024-{m:02d}-05", 1000.0, prior=3) for m in range(1, 13)] + \
               [ev(f"2024-{m:02d}-06", 0.0, prior=0) for m in range(1, 13)]
        t = ls.monthly_test(evts, lambda e: e["prior"] >= 2, lambda e: e["prior"] <= 1, (-5, 5))
        self.assertAlmostEqual(t["mean_diff"], 5.0)

    def test_verdict_branches(self):
        self.assertEqual(ls.verdict({"months": 20, "mean_diff": 1.0, "t_hac": 2.5}), "RULE_PREDICTS")
        self.assertEqual(ls.verdict({"months": 20, "mean_diff": -1.0, "t_hac": -2.5}), "RULE_MISLEADS")
        self.assertEqual(ls.verdict({"months": 20, "mean_diff": 1.0, "t_hac": 1.5}), "INCONCLUSIVE")
        self.assertEqual(ls.verdict({"months": 11, "mean_diff": 1.0, "t_hac": 3.0}), "INCONCLUSIVE")   # too few
        self.assertEqual(ls.verdict({"months": 20, "mean_diff": -1.0, "t_hac": 2.5}), "INCONCLUSIVE")  # sign ≠ t
        self.assertEqual(ls.verdict({"months": 20, "mean_diff": None, "t_hac": None}), "INCONCLUSIVE")

    def test_summarize_groups_and_verdicts(self):
        evts = []
        for y in (2024, 2025):
            for m in range(1, 13):
                d = f"{y}-{m:02d}-1"
                evts += [ev(d + "0", 3.0 + (m % 3), prior=3, mf="Strong"), ev(d + "1", 2.0 + (m % 2), prior=2),
                         ev(d + "2", -1.0 - (m % 2), prior=1), ev(d + "3", -2.0, prior=0)]
        evts.append(ev("2024-01-01", 9.0, prior=None))                  # not scored
        s = ls.summarize(evts)
        self.assertEqual((s["events"], s["scored"]), (97, 96))
        self.assertEqual(s["by_prior"]["3"]["n"], 24)
        self.assertEqual(s["verdict"], "RULE_PREDICTS")
        self.assertEqual(s["verdict_mf"], "RULE_PREDICTS")
        self.assertEqual(sorted(s["per_year"]), ["2024", "2025"])


if __name__ == "__main__":
    unittest.main()
