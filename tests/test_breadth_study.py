"""Tests for scripts/backtesting/breadth_study.py (R&D #83): the breadth override of
PROFILE_RULES (restored afterwards, also on error), the point-in-time score ranker, win rates,
and both pre-registered verdicts, with fixtures where picking on the test half would differ."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
import breadth_study as bs  # noqa: E402
import portfolio_backtest as pb  # noqa: E402


def summary(train_med, test_med, draws):
    """train_med/test_med: {variant: median Sharpe}; draws: [{"train": {v: sh}, "test": {v: sh}}]."""
    return {"periods": {"train": {v: {"sharpe": {"median": m}} for v, m in train_med.items()},
                        "test": {v: {"sharpe": {"median": m}} for v, m in test_med.items()}},
            "per_draw_sharpe": draws}


ALL = ("aether_rules", "breadth_10", "breadth_20", "breadth_40", "score_tilt_q", "spy_buy_hold")


def draws(n, train, test):
    """n identical draws with the given per-variant Sharpes."""
    return [{"train": dict(train), "test": dict(test)} for _ in range(n)]


class TestOverrides(unittest.TestCase):
    def test_profile_breadth_sets_slots_and_cap_then_restores(self):
        before = pb.PROFILE_RULES
        with bs.profile_breadth(20) as rules:
            self.assertIs(pb.PROFILE_RULES, rules)
            self.assertEqual({r["max_positions"] for r in rules.values()}, {20})
            self.assertEqual({r["max_allocation_pct"] for r in rules.values()}, {0.05})
            self.assertEqual(rules["BALANCED"]["min_score_threshold"], before["BALANCED"]["min_score_threshold"])
        self.assertIs(pb.PROFILE_RULES, before)
        with self.assertRaises(RuntimeError), bs.profile_breadth(10):
            raise RuntimeError("x")
        self.assertIs(pb.PROFILE_RULES, before)

    def test_small_breadth_keeps_todays_tighter_cap(self):
        with bs.profile_breadth(6) as rules:
            self.assertEqual(rules["AGGRESSIVE"]["max_allocation_pct"], 0.15)      # min(0.15, 1/6)
            self.assertEqual(rules["DEFENSIVE"]["max_allocation_pct"], 0.10)       # min(0.10, 1/6)

    def test_score_ranker_is_point_in_time_and_takes_the_top_fifth(self):
        panel = {f"S{i}": {"2030-01-02": [i, 0]} for i in range(10)}
        panel["LATE"] = {"2030-01-03": [100, 0]}                                     # not scored that day
        bars = {s: {"idx": {"2030-01-02": 0, "2030-01-03": 1}} for s in panel}
        rank = bs.score_ranker(panel)(bars, list(panel), "2030-01-02", "score")
        self.assertEqual([s for _sc, s in rank], ["S9", "S8"])                       # 20% of 10, best first
        self.assertEqual(bs.score_ranker(panel)(bars, ["S1"], "2030-01-09", "score"), [])


class TestVerdicts(unittest.TestCase):
    def test_win_rates_skip_missing(self):
        d = [{"train": {"a": 1, "b": 0}}, {"train": {"a": 0, "b": 1}}, {"train": {"a": 1}}]
        self.assertEqual(bs.win_rates(d, "a", "b", "train"), 0.5)
        self.assertIsNone(bs.win_rates([{"train": {}}], "a", "b", "train"))

    def test_breadth_helps_and_the_pick_is_on_train(self):
        train = {"aether_rules": 0.5, "breadth_10": 0.6, "breadth_20": 0.9, "breadth_40": 0.7,
                 "score_tilt_q": 0.4, "spy_buy_hold": 1.2}
        test = {**train, "breadth_10": 2.0}                                          # best test, not picked
        s = summary(train, test, draws(10, train, {**test, "breadth_20": 1.0}))
        v = bs.verdicts(s)
        self.assertEqual((v["breadth"]["picked"], v["breadth"]["verdict"]), ("breadth_20", "BREADTH_HELPS"))
        self.assertEqual(v["vs_spy"]["verdict"], "NO_EDGE_VS_SPY")                  # nothing beats SPY

    def test_breadth_hurts_and_inconclusive(self):
        train = {"aether_rules": 1.0, "breadth_10": 0.6, "breadth_20": 0.5, "breadth_40": 0.4,
                 "score_tilt_q": 0.4, "spy_buy_hold": 0.2}
        self.assertEqual(bs.verdicts(summary(train, train, draws(10, train, train)))["breadth"]["verdict"],
                         "BREADTH_HURTS")
        mixed = draws(5, train, train) + draws(5, {**train, "breadth_10": 2.0}, {**train, "breadth_10": 2.0})
        self.assertEqual(bs.verdicts(summary(train, train, mixed))["breadth"]["verdict"], "INCONCLUSIVE")

    def test_beats_spy_needs_both_halves(self):
        train = {"aether_rules": 0.5, "breadth_10": 0.6, "breadth_20": 1.5, "breadth_40": 0.7,
                 "score_tilt_q": 0.4, "spy_buy_hold": 1.0}
        good = bs.verdicts(summary(train, train, draws(10, train, train)))
        self.assertEqual((good["vs_spy"]["picked"], good["vs_spy"]["verdict"]), ("breadth_20", "BEATS_SPY"))
        bad_test = draws(10, train, {**train, "breadth_20": 0.9})
        self.assertEqual(bs.verdicts(summary(train, train, bad_test))["vs_spy"]["verdict"], "NO_EDGE_VS_SPY")
        self.assertEqual(set(good["win_vs_spy_all"]), set(bs.STRATEGIES))


if __name__ == "__main__":
    unittest.main()
