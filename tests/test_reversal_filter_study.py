"""Tests for scripts/backtesting/reversal_filter_study.py (R&D #86): the 3-session return, the
daily run-up sets (top third of the scored names), the admit hook, the reversal ranker (bottom
fifth, point-in-time) and the two pre-registered verdicts."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
import reversal_filter_study as rs  # noqa: E402

DAYS = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"]


def bars_for(closes):
    return {"idx": {d: i for i, d in enumerate(DAYS)}, "c": list(closes)}


def universe(n=12):
    """n symbols; symbol k rises k% over the last 3 sessions to DAYS[4]."""
    bars = {f"S{k}": bars_for([100, 100, 100, 100, 100 + k]) for k in range(n)}
    panel = {s: {DAYS[4]: [1.0, 1.0]} for s in bars}
    return panel, bars


class TestRet3(unittest.TestCase):
    def test_value_and_missing(self):
        b = {"X": bars_for([100, 90, 95, 110, 99])}
        self.assertAlmostEqual(rs.ret3(b, "X", DAYS[4]), 99 / 90 - 1)       # vs the close 3 sessions back
        self.assertIsNone(rs.ret3(b, "X", DAYS[2]))                          # fewer than 3 prior bars
        self.assertIsNone(rs.ret3(b, "Y", DAYS[4]))


class TestRunup(unittest.TestCase):
    def test_top_third_of_scored_names(self):
        panel, bars = universe(12)
        sets = rs.runup_sets(panel, bars, DAYS, frozenset())
        self.assertEqual(sets[DAYS[4]], {"S11", "S10", "S9", "S8"})          # ceil(12/3) = 4 biggest risers
        self.assertNotIn(DAYS[3], sets)                                      # nobody scored that day

    def test_excluded_and_unscored_left_out_and_min_names(self):
        panel, bars = universe(12)
        sets = rs.runup_sets(panel, bars, DAYS, frozenset({"S11"}))
        self.assertEqual(sets[DAYS[4]], {"S10", "S9", "S8", "S7"})           # 11 names: ceil = 4
        panel, bars = universe(9)
        self.assertEqual(rs.runup_sets(panel, bars, DAYS, frozenset()), {})  # < 10 names: no filter

    def test_admit(self):
        admit = rs.no_runup_admit({DAYS[4]: {"S11"}})
        self.assertFalse(admit("S11", DAYS[4]))
        self.assertTrue(admit("S1", DAYS[4]))
        self.assertTrue(admit("S11", DAYS[3]))                               # no set that day: allowed


class TestRanker(unittest.TestCase):
    def test_bottom_fifth_biggest_loser_first(self):
        bars = {f"S{k}": bars_for([100, 100, 100, 100, 100 - k]) for k in range(12)}
        panel = {s: {DAYS[4]: [1.0, 1.0]} for s in bars}
        rank = rs.reversal_ranker(panel, frozenset())
        got = [s for _v, s in rank(bars, list(bars), DAYS[4], "ret3")]
        self.assertEqual(got, ["S11", "S10", "S9"])                          # ceil(12 * 0.2) = 3

    def test_universe_and_point_in_time(self):
        bars = {f"S{k}": bars_for([100, 100, 100, 100, 100 - k]) for k in range(12)}
        panel = {s: {DAYS[4]: [1.0, 1.0]} for s in bars}
        panel["S11"] = {DAYS[0]: [1.0, 1.0]}                                 # not scored on the day
        rank = rs.reversal_ranker(panel, frozenset())
        got = [s for _v, s in rank(bars, [s for s in bars if s != "S10"], DAYS[4], "ret3")]
        self.assertEqual(got[0], "S9")                                       # S11 unscored, S10 outside
        self.assertEqual(rank(bars, ["S1", "S2"], DAYS[4], "ret3"), [])      # < 10 names


def _summary(no_runup, rev, base=1.0, spy=1.0, n=10, test_no_runup=None, test_rev=None):
    def draw(nr, rv):
        return {"aether_rules": base, "aether_no_runup": nr, "reversal_q": rv, "spy_buy_hold": spy,
                "rsp_buy_hold": spy}
    return {"per_draw_sharpe": [{"train": draw(no_runup, rev),
                                 "test": draw(test_no_runup if test_no_runup is not None else no_runup,
                                              test_rev if test_rev is not None else rev)}
                                for _ in range(n)]}


class TestVerdicts(unittest.TestCase):
    def test_filter(self):
        self.assertEqual(rs.verdicts(_summary(1.5, 0.0))["filter"]["verdict"], "FILTER_HELPS")
        self.assertEqual(rs.verdicts(_summary(0.5, 0.0))["filter"]["verdict"], "FILTER_HURTS")
        self.assertEqual(rs.verdicts(_summary(1.5, 0.0, test_no_runup=0.5))["filter"]["verdict"], "INCONCLUSIVE")
        self.assertEqual(rs.verdicts(_summary(1.0, 0.0))["filter"]["verdict"], "FILTER_HURTS")   # ties don't win

    def test_sleeve_needs_both_halves(self):
        self.assertEqual(rs.verdicts(_summary(1.0, 2.0))["sleeve"]["verdict"], "BEATS_SPY")
        self.assertEqual(rs.verdicts(_summary(1.0, 2.0, test_rev=0.5))["sleeve"]["verdict"], "NO_EDGE_VS_SPY")
        self.assertEqual(rs.verdicts(_summary(1.0, 2.0, spy=3.0))["sleeve"]["verdict"], "NO_EDGE_VS_SPY")

    def test_win_rate_skips_missing(self):
        pds = [{"train": {"a": 2.0, "b": 1.0}}, {"train": {"a": None, "b": 1.0}}, {"train": {"a": 0.0, "b": 1.0}}]
        self.assertEqual(rs.win_rate(pds, "a", "b", "train"), 0.5)
        self.assertIsNone(rs.win_rate([{"train": {"a": None, "b": 1.0}}], "a", "b", "train"))


if __name__ == "__main__":
    unittest.main()
