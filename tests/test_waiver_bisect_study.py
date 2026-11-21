"""R&D #112 waiver bisect study: pick selection, next-close outcome, day pairing, verdict."""
import datetime
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts", "backtesting"))
sys.path.insert(0, ROOT)

import backtest_ratings as br
import waiver_bisect_study as ws


def _row(day, sym, v2, v1=None, v0=None, x=None):
    return {"day": day, "sym": sym, "s": {"V2": v2, "V1": v2 if v1 is None else v1, "V0": v2 if v0 is None else v0},
            "x": x}


def _days(start, n):
    d0 = datetime.date.fromisoformat(start)
    return [(d0 + datetime.timedelta(days=k)).isoformat() for k in range(n)]


class TestSelectPicks(unittest.TestCase):
    def test_threshold_top_n_and_symbol_tiebreak(self):
        rows = [_row("2024-01-02", s, sc) for s, sc in
                [("ZZ", 5.0), ("AA", 5.0), ("MM", 1.9), ("BB", 2.0), ("CC", 9.0), ("DD", 3.0), ("EE", 3.0),
                 ("FF", 4.0), ("GG", 2.5)]]
        picks = ws.select_picks(rows, "V2")["2024-01-02"]
        # MM is below 2.0; 8 pass; top 6 by score, AA before ZZ on the 5.0 tie, DD before EE on 3.0.
        self.assertEqual([r["sym"] for r in picks], ["CC", "AA", "ZZ", "FF", "DD", "EE"])

    def test_threshold_is_inclusive_and_per_variant(self):
        rows = [_row("2024-01-02", "AA", 2.0, v1=1.9), _row("2024-01-02", "BB", 3.0, v1=2.0)]
        self.assertEqual([r["sym"] for r in ws.select_picks(rows, "V2")["2024-01-02"]], ["BB", "AA"])
        self.assertEqual([r["sym"] for r in ws.select_picks(rows, "V1")["2024-01-02"]], ["BB"])

    def test_day_without_candidates_absent(self):
        self.assertEqual(ws.select_picks([_row("2024-01-02", "AA", 1.0)], "V2"), {})

    def test_variant_ranking_changes_picks(self):
        # Seven names: V2 ranks GG first, V1 drops it to last -> different 6.
        rows = [_row("2024-01-02", s, 3.0, v1=3.0) for s in "ABCDEF"]
        rows.append(_row("2024-01-02", "GG", 8.0, v1=2.1))
        self.assertIn("GG", [r["sym"] for r in ws.select_picks(rows, "V2")["2024-01-02"]])
        self.assertNotIn("GG", [r["sym"] for r in ws.select_picks(rows, "V1")["2024-01-02"]])
        sd = ws.share_different(ws.select_picks(rows, "V1"), ws.select_picks(rows, "V2"))
        self.assertEqual((sd["days"], sd["days_different"]), (1, 1))


class TestOutcome(unittest.TestCase):
    def setUp(self):
        days = _days("2024-01-01", 30)
        self.adj = {"days": days, "idx": {d: i for i, d in enumerate(days)}, "c": [100.0 + i for i in range(30)]}

    def test_starts_at_next_close(self):
        d1, d2, ret = ws.forward_window(self.adj, "2024-01-05")       # index 4
        self.assertEqual((d1, d2), ("2024-01-06", "2024-01-16"))       # indices 5 and 15
        self.assertAlmostEqual(ret, 115.0 / 105.0 - 1)

    def test_non_session_day_uses_first_bar_after(self):
        adj = dict(self.adj, days=[d for d in self.adj["days"] if d != "2024-01-06"])
        adj["c"] = self.adj["c"][:5] + self.adj["c"][6:]
        d1, _, _ = ws.forward_window(adj, "2024-01-06")
        self.assertEqual(d1, "2024-01-07")

    def test_no_window_at_end(self):
        self.assertIsNone(ws.forward_window(self.adj, self.adj["days"][-11]))   # t+1 = 20, t+11 = 30: past the end
        self.assertIsNotNone(ws.forward_window(self.adj, self.adj["days"][-12]))

    def test_excess_vs_spy_same_dates(self):
        spy = {"days": self.adj["days"], "idx": self.adj["idx"], "c": [200.0] * 30}
        spy["c"][15] = 210.0                                            # SPY +5% from day 5 to day 15
        row = {"ret": 0.10, "d1": "2024-01-06", "d2": "2024-01-16"}
        self.assertAlmostEqual(ws.excess_pp(row, spy), 5.0)
        self.assertIsNone(ws.excess_pp({"ret": 0.1, "d1": "1999-01-01", "d2": "2024-01-16"}, spy))
        self.assertIsNone(ws.excess_pp({"ret": None, "d1": None, "d2": None}, spy))

    def test_last_full_day(self):
        last = ws.last_full_day(self.adj)
        self.assertIsNotNone(ws.forward_window(self.adj, last))
        nxt = self.adj["days"][self.adj["idx"][last] + 1]
        self.assertIsNone(ws.forward_window(self.adj, nxt))


class TestPairing(unittest.TestCase):
    def test_day_means_skip_missing_outcomes(self):
        picks = {"d1": [{"x": 1.0}, {"x": None}, {"x": 3.0}], "d2": [{"x": None}]}
        self.assertEqual(ws.day_means(picks), {"d1": 2.0})

    def test_paired_diff_only_common_days(self):
        self.assertEqual(ws.paired_diff({"a": 1.0, "b": 2.0, "c": 5.0}, {"b": 0.5, "c": 6.0, "d": 1.0}),
                         [("b", 1.5), ("c", -1.0)])


class TestVerdict(unittest.TestCase):
    def test_branches(self):
        self.assertEqual(ws.verdict(0.5, 2.0, [0.1, 0.2], "REV"), "REV")
        self.assertEqual(ws.verdict(-0.5, -2.0, [-0.1, -0.2], "REV"), "KEEP_SETUP_WAIVER")
        self.assertEqual(ws.verdict(0.5, 1.95, [0.1, 0.2], "REV"), "INCONCLUSIVE")
        self.assertEqual(ws.verdict(-0.5, -1.95, [-0.1, -0.2], "REV"), "INCONCLUSIVE")
        self.assertEqual(ws.verdict(0.5, 1.96, [0.1, 0.2], "REV"), "REV")          # inclusive gate
        self.assertEqual(ws.verdict(-0.5, -1.96, [-0.1, -0.2], "REV"), "KEEP_SETUP_WAIVER")

    def test_halves_must_agree(self):
        self.assertEqual(ws.verdict(0.5, 3.0, [0.9, -0.1], "REV"), "INCONCLUSIVE")
        self.assertEqual(ws.verdict(0.5, 3.0, [-0.1, 0.9], "REV"), "INCONCLUSIVE")
        self.assertEqual(ws.verdict(-0.5, -3.0, [0.1, -0.9], "REV"), "INCONCLUSIVE")
        self.assertEqual(ws.verdict(-0.5, -3.0, [-0.9, 0.1], "REV"), "INCONCLUSIVE")

    def test_sign_must_match_t(self):
        self.assertEqual(ws.verdict(-0.1, 2.5, [0.1, 0.1], "REV"), "INCONCLUSIVE")
        self.assertEqual(ws.verdict(0.1, -2.5, [-0.1, -0.1], "REV"), "INCONCLUSIVE")

    def test_missing_stat_is_inconclusive(self):
        self.assertEqual(ws.verdict(0.5, None, [0.1, 0.2], "REV"), "INCONCLUSIVE")
        self.assertEqual(ws.verdict(0.5, 3.0, [None, 0.2], "REV"), "INCONCLUSIVE")

    def test_compare_decides_on_hac_not_plain_t(self):
        # Blocks of 10 equal values: plain t 2.31 but Newey-West (lag 9) t 1.21 -> INCONCLUSIVE.
        xs = ([1.0] * 10 + [-0.65] * 10) * 6
        days = _days("2024-07-01", 60) + _days("2025-01-01", 60)
        c = ws.compare(list(zip(days, xs, strict=True)), "REV")
        self.assertGreaterEqual(c["t_plain"], 1.96)
        self.assertLess(c["t_hac"], 1.96)
        self.assertGreater(c["half1_mean_pp"], 0)
        self.assertGreater(c["half2_mean_pp"], 0)
        self.assertEqual(c["verdict"], "INCONCLUSIVE")

    def test_compare_splits_halves_at_2025(self):
        days = _days("2024-12-30", 4)                    # 12-30, 12-31 | 01-01, 01-02
        c = ws.compare(list(zip(days, [1.0, 3.0, -1.0, -2.0], strict=True)), "REV")
        self.assertEqual((c["half1_days"], c["half2_days"]), (2, 2))
        self.assertEqual((c["half1_mean_pp"], c["half2_mean_pp"]), (2.0, -1.5))

    def test_compare_passes_on_strong_consistent_series(self):
        xs = [1.0 + (0.3 if k % 2 else -0.3) for k in range(120)]
        days = _days("2024-07-01", 60) + _days("2025-01-01", 60)
        self.assertEqual(ws.compare(list(zip(days, xs, strict=True)), "REV")["verdict"], "REV")
        neg = [-x for x in xs]
        self.assertEqual(ws.compare(list(zip(days, neg, strict=True)), "REV")["verdict"], "KEEP_SETUP_WAIVER")


class TestSummarize(unittest.TestCase):
    def test_v1_minus_v2_end_to_end(self):
        # Two days. Day 1: V2 picks the waived name W (bad), V1 picks plain P (good). Day 2: same picks.
        rows = []
        for d in ("2024-03-01", "2025-03-03"):
            rows.append(_row(d, "W", 5.0, v1=1.0, v0=1.0, x=-4.0))
            rows.append(_row(d, "P", 3.0, v1=3.0, v0=3.0, x=2.0))
        rep = ws.summarize(rows)
        c = rep["V1_minus_V2"]
        self.assertEqual(c["days"], 2)
        self.assertAlmostEqual(c["mean_pp"], 2.0 - (-1.0))             # V2 mean of (-4, 2) = -1
        self.assertEqual(rep["variants"]["V1"]["picks"], 2)
        self.assertEqual(rep["variants"]["V2"]["picks"], 4)
        self.assertEqual(rep["share_days_different"]["V1_vs_V2"]["days_different"], 2)
        # A window filter drops the 2025 day.
        self.assertEqual(ws.summarize(rows, hi="2024-12-31")["V1_minus_V2"]["days"], 1)


class TestCaptureFields(unittest.TestCase):
    def test_capture_restores_hook_and_matches_score(self):
        dates = _days("2024-01-01", 40)
        ts = {d: {"1. open": "100", "2. high": "101", "3. low": "99", "4. close": str(100 + i * 0.5),
                  "5. volume": "1000"} for i, d in enumerate(dates)}
        data = {"checklist_stocks": {"ltTrend": "Strong", "moneyFlow": "Strong", "overboughtOversold": "Optimal",
                                     "industry": "Strong"}, "metaInfo": [{"symbol": "TEST", "Last": 119.5}]}
        before = br._short_score_fn
        res, fields = ws.capture_fields(data, data, 119.5, 39, dates, ts, br.SeasonalitySlots({}))
        self.assertIs(br._short_score_fn, before)
        self.assertEqual(fields["industry_strength"], "Strong")
        self.assertEqual(ws.short_score(fields), res[1])
        self.assertIn("setup_ok", fields)


if __name__ == "__main__":
    unittest.main()
