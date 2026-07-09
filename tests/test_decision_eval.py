"""
Tests for decision_eval.py — logging, backtracking scorer, reflection.
Forward prices are injected; no real OHLCV or AI calls.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import decision_eval as de


def _entry(sym, action, price, cost, verdicts=None, date="2026-06-01"):
    return {"date": date, "symbol": sym, "price": price, "cost": cost,
            "s10": 0, "l60": 0, "pnl_pct": ((price - cost) / cost * 100) if cost else None,
            "rules_action": action, "rules_reason": "test", "verdicts": verdicts or {}}


class TestLogRoundTrip(unittest.TestCase):
    def test_append_and_read(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False); tmp.close()
        try:
            de.log_decisions([_entry("AAA", "SELL", 10, 8)], path=tmp.name)
            de.log_decisions([_entry("BBB", "HOLD", 20, 18)], path=tmp.name)
            rows = de.read_log(path=tmp.name)
            self.assertEqual([r["symbol"] for r in rows], ["AAA", "BBB"])
        finally:
            os.unlink(tmp.name)

    def test_read_missing_file(self):
        self.assertEqual(de.read_log(path="/nonexistent/x.jsonl"), [])


class TestScoring(unittest.TestCase):
    def _score(self, entries, fwd):
        return de.score_log(entries, horizon_days=10, fwd_price_fn=lambda s, d, h: fwd.get(s))

    def test_sell_that_fell_is_correct(self):
        sc = self._score([_entry("AAA", "SELL", 100, 90)], {"AAA": 80})
        r = sc["selectors"]["rules"]
        self.assertEqual(r["correct"], 1)
        self.assertEqual(r["winner_sell_miss"], 0)
        self.assertGreater(r["avoided_loss_pct"], 0)

    def test_sell_winner_that_rose_is_miss(self):
        # In profit (price>cost) SELL, then rose -> winner-selling miss.
        sc = self._score([_entry("RPD", "SELL", 100, 60)], {"RPD": 120})
        r = sc["selectors"]["rules"]
        self.assertEqual(r["correct"], 0)
        self.assertEqual(r["winner_sell_miss"], 1)
        self.assertGreater(r["missed_upside_pct"], 0)
        self.assertEqual(sc["winner_selling_misses"][0]["symbol"], "RPD")

    def test_sell_loser_that_rose_not_winner_miss(self):
        # SELL of a losing position (price<cost) that rose: incorrect, but NOT a
        # winner-selling miss (it wasn't in profit).
        sc = self._score([_entry("XXX", "SELL", 100, 120)], {"XXX": 110})
        r = sc["selectors"]["rules"]
        self.assertEqual(r["correct"], 0)
        self.assertEqual(r["winner_sell_miss"], 0)

    def test_hold_that_rose_is_correct(self):
        sc = self._score([_entry("HHH", "HOLD", 100, 90)], {"HHH": 115})
        self.assertEqual(sc["selectors"]["rules"]["correct"], 1)

    def test_review_that_rose_is_correct(self):
        sc = self._score([_entry("FLW", "REVIEW", 100, 80)], {"FLW": 130})
        self.assertEqual(sc["selectors"]["rules"]["correct"], 1)

    def test_no_forward_data_skipped(self):
        sc = self._score([_entry("AAA", "SELL", 100, 90)], {})  # fwd None
        self.assertEqual(sc["selectors"], {})

    def test_provider_flag_correct_when_rules_wrong(self):
        # rules SELL a winner that rose (rules wrong); FLAG-FOR-REVIEW was right.
        e = _entry("RPD", "SELL", 100, 60,
                   verdicts={"gpt": {"verdict": "FLAG-FOR-REVIEW", "note": "winner"}})
        sc = self._score([e], {"RPD": 120})
        self.assertEqual(sc["selectors"]["gpt"]["correct"], 1)

    def test_provider_agree_correct_when_rules_right(self):
        e = _entry("AAA", "SELL", 100, 90,
                   verdicts={"gpt": {"verdict": "AGREE", "note": "ok"}})
        sc = self._score([e], {"AAA": 80})
        self.assertEqual(sc["selectors"]["gpt"]["correct"], 1)

    def test_provider_flag_when_rules_right_is_not_useful(self):
        e = _entry("AAA", "SELL", 100, 90,
                   verdicts={"gpt": {"verdict": "FLAG-FOR-REVIEW", "note": "?"}})
        sc = self._score([e], {"AAA": 80})   # rules right (fell); flagging was not useful
        self.assertEqual(sc["selectors"]["gpt"]["correct"], 0)

    def test_hit_rate_aggregates(self):
        entries = [_entry("A", "SELL", 100, 90), _entry("B", "SELL", 100, 90),
                   _entry("C", "SELL", 100, 90)]
        sc = self._score(entries, {"A": 80, "B": 80, "C": 120})  # 2 right, 1 wrong
        self.assertEqual(sc["selectors"]["rules"]["scored"], 3)
        self.assertAlmostEqual(sc["selectors"]["rules"]["hit_rate"], 66.7, places=1)


class TestReflection(unittest.TestCase):
    def test_reflection_mentions_misses(self):
        sc = de.score_log([_entry("RPD", "SELL", 100, 60)], horizon_days=10,
                          fwd_price_fn=lambda s, d, h: 120)
        text = de.reflection(sc)
        self.assertIn("RPD", text)
        self.assertIn("winner-sell", text.lower())

    def test_reflection_clean_window(self):
        sc = de.score_log([_entry("A", "SELL", 100, 90)], horizon_days=10,
                          fwd_price_fn=lambda s, d, h: 80)
        self.assertIn("No winner-selling misses", de.reflection(sc))


if __name__ == "__main__":
    unittest.main()
