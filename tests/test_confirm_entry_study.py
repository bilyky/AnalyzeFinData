"""Tests for scripts/backtesting/confirm_entry_study.py (R&D #92): the confirmation window (after
the event only, first firing per signal, 'any'), the production-detector wiring, the monthly test
and the verdicts."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
import confirm_entry_study as ce  # noqa: E402

DAYS = [f"2015-01-{i + 1:02d}" for i in range(31)]


class TestWindow(unittest.TestCase):
    def test_first_firing_after_the_event_only(self):
        fires = {DAYS[5]: {"rsidiv"}, DAYS[7]: {"vic2b", "rsidiv"}, DAYS[9]: {"vic123"}}
        got = ce.first_confirmations(DAYS, 5, lambda d: fires.get(d, set()))
        self.assertEqual(got, {"vic2b": 7, "rsidiv": 7, "vic123": 9, "any": 7})   # day 5 itself excluded

    def test_window_is_twenty_sessions(self):
        fires = {DAYS[26]: {"vic2b"}}
        self.assertEqual(ce.first_confirmations(DAYS, 5, lambda d: fires.get(d, set())), {})
        fires = {DAYS[25]: {"vic2b"}}
        self.assertEqual(ce.first_confirmations(DAYS, 5, lambda d: fires.get(d, set()))["vic2b"], 25)

    def test_a_signal_firing_twice_keeps_its_first_day(self):
        fires = {DAYS[7]: {"vic2b"}, DAYS[8]: {"vic2b"}}
        self.assertEqual(ce.first_confirmations(DAYS, 5, lambda d: fires.get(d, set()))["vic2b"], 7)

    def test_nothing_fires(self):
        self.assertEqual(ce.first_confirmations(DAYS, 0, lambda d: set()), {})


class TestDetectors(unittest.TestCase):
    def test_wiring(self):
        with mock.patch.object(ce.patterns, "chart_pattern_score", return_value=(2.0, ["Vic2B↑", "MACD+"])), \
                mock.patch.object(ce.scoring, "rsi_divergence_score", return_value=-0.5):
            self.assertEqual(ce.signals_on({}, "2015-01-02"), {"vic2b"})
        with mock.patch.object(ce.patterns, "chart_pattern_score", return_value=(1.0, ["Vic123↑"])), \
                mock.patch.object(ce.scoring, "rsi_divergence_score", return_value=0.5):
            self.assertEqual(ce.signals_on({}, "2015-01-02"), {"vic123", "rsidiv"})


class TestStats(unittest.TestCase):
    def test_verdicts(self):
        pts = [(f"20{10 + m // 12}-{1 + m % 12:02d}-05", 1.0 + (m % 4)) for m in range(40) for _ in range(4)]
        full = ce.monthly(pts)
        self.assertEqual((full["entries"], full["months"]), (160, 40))
        pos = {"mean": 1.0}
        self.assertEqual(ce.verdict(full, pos, pos, "UP", "DOWN"), "UP")
        self.assertEqual(ce.verdict(full, {"mean": -1.0}, pos, "UP", "DOWN"), "INCONCLUSIVE")
        self.assertEqual(ce.verdict(dict(full, mean=-1.0, t_hac=-2.5), {}, {}, "UP", "DOWN"), "DOWN")
        self.assertEqual(ce.verdict(dict(full, t_hac=1.0), pos, pos, "UP", "DOWN"), "INCONCLUSIVE")
        self.assertEqual(ce.verdict(dict(full, entries=99), pos, pos, "UP", "DOWN"), "INSUFFICIENT")
        self.assertEqual(ce.verdict(dict(full, months=23), pos, pos, "UP", "DOWN"), "INSUFFICIENT")

    def test_judge_dates_and_pairs(self):
        rows = [{"date": "2013-12-20", "vic2b": {"day": "2014-01-03", "conf_vs_spy": 5.0, "paired": 1.0}},
                {"date": "2013-11-01", "vic2b": {"day": "2013-11-05", "conf_vs_spy": -2.0, "paired": -1.0}},
                {"date": "2013-10-01"}]
        h1 = ce.judge(rows, "vic2b", "conf_vs_spy", "conf", "U", "D")
        self.assertEqual((h1["before"]["entries"], h1["from"]["entries"]), (1, 1))  # split on the entry day
        h2 = ce.judge(rows, "vic2b", "paired", "event", "U", "D")
        self.assertEqual((h2["before"]["entries"], h2["from"]["entries"]), (2, 0))  # split on the event day

    def test_describe_and_labels(self):
        rows = [{"qa": "good", "vic2b": {"wait": 3, "price_vs_event": 2.0, "lower_low_first": 1.0, "conf_vs_spy": 1.0}}] * 30 \
            + [{"qa": "junk"}]
        d = ce.describe(rows, "vic2b")
        self.assertEqual((d["fires"], d["median_wait"]), (30, 3.0))
        self.assertEqual(ce.by_label(rows, "vic2b", "qa"), {"good": {"n": 30, "mean": 1.0, "median": 1.0, "beat_spy": 1.0}})


if __name__ == "__main__":
    unittest.main()
