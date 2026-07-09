"""
Tests for decision_eval.py — logging, backtracking scorer, reflection.
Forward prices are injected; no real OHLCV or AI calls.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import decision_eval as de


def _write_ohlcv(closes_by_date):
    """Write a temp OHLCV JSON file and return its directory + symbol."""
    d = tempfile.mkdtemp()
    ts = {day: {"4. close": str(c)} for day, c in closes_by_date.items()}
    with open(os.path.join(d, "TST_daily.json"), "w") as f:
        json.dump({"Time Series (Daily)": ts}, f)
    return d


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

    def test_skips_malformed_line(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w");
        tmp.write('{"symbol": "AAA"}\n')
        tmp.write('this is not json\n')        # a half-written crash line
        tmp.write('{"symbol": "BBB"}\n')
        tmp.close()
        try:
            rows = de.read_log(path=tmp.name)
            self.assertEqual([r["symbol"] for r in rows], ["AAA", "BBB"])
        finally:
            os.unlink(tmp.name)


class TestBuildEntry(unittest.TestCase):
    def test_no_shadow_records_action_without_verdicts(self):
        # Winner on a soft signal, above its 50-DMA -> REVIEW, no AI calls.
        e = de.build_entry("RPD", price=100, cost=60, stop_loss=50,
                           s10=-4, l60=-2, sma50=90, date="2026-06-01", run_shadow=False)
        self.assertEqual(e["rules_action"], "REVIEW")
        self.assertEqual(e["verdicts"], {})
        self.assertAlmostEqual(e["pnl_pct"], (100 - 60) / 60 * 100, places=3)

    def test_stop_breach_records_sell(self):
        e = de.build_entry("XXX", price=49, cost=60, stop_loss=50,
                           s10=0, l60=0, date="2026-06-01", run_shadow=False)
        self.assertEqual(e["rules_action"], "SELL")

    def test_auto_shadow_skips_ai_on_hold(self):
        # run_shadow=None + a HOLD action must make zero AI calls.
        with mock.patch("ai_client.enabled_providers") as ep:
            e = de.build_entry("AAA", price=100, cost=90, stop_loss=50,
                               s10=5, l60=5, date="2026-06-01", run_shadow=None)
            self.assertEqual(e["rules_action"], "HOLD")
            self.assertEqual(e["verdicts"], {})
            ep.assert_not_called()


class TestLogTrim(unittest.TestCase):
    def test_trims_to_cap(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False); tmp.close()
        try:
            with open(tmp.name, "w") as f:
                for i in range(120):
                    f.write(json.dumps({"symbol": f"S{i}", "date": "2026-06-01"}) + "\n")
            de._trim_log(tmp.name, max_lines=50)
            rows = de.read_log(path=tmp.name)
            self.assertEqual(len(rows), 50)
            self.assertEqual(rows[0]["symbol"], "S70")   # oldest kept
            self.assertEqual(rows[-1]["symbol"], "S119")  # newest kept
        finally:
            os.unlink(tmp.name)

    def test_no_trim_under_cap(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False); tmp.close()
        try:
            with open(tmp.name, "w") as f:
                f.write(json.dumps({"symbol": "A", "date": "2026-06-01"}) + "\n")
            de._trim_log(tmp.name, max_lines=50)
            self.assertEqual(len(de.read_log(path=tmp.name)), 1)
        finally:
            os.unlink(tmp.name)


class TestForwardClose(unittest.TestCase):
    def setUp(self):
        self._orig = de.OHLCV_DIR
    def tearDown(self):
        de.OHLCV_DIR = self._orig

    def test_none_until_horizon_matures(self):
        de.OHLCV_DIR = Path(_write_ohlcv({"2026-06-02": 10, "2026-06-03": 11}))
        self.assertIsNone(de.ohlcv_forward_close("TST", "2026-06-01", 10))

    def test_returns_close_at_horizon(self):
        de.OHLCV_DIR = Path(_write_ohlcv({f"2026-06-{i:02d}": 10 + i for i in range(2, 15)}))
        # 3rd forward trading day after 06-01 is 06-04 -> close 14
        self.assertEqual(de.ohlcv_forward_close("TST", "2026-06-01", 3), 14.0)

    def test_missing_date_or_symbol(self):
        self.assertIsNone(de.ohlcv_forward_close("TST", None, 10))
        self.assertIsNone(de.ohlcv_forward_close(None, "2026-06-01", 10))


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

    def test_dedup_same_symbol_date_keeps_last(self):
        # Same symbol/day logged twice (scheduled run + manual re-run); the final
        # decision (SELL) is what stands, and it must be scored exactly once.
        entries = [_entry("A", "HOLD", 100, 90, date="2026-06-01"),
                   _entry("A", "SELL", 100, 90, date="2026-06-01")]
        sc = self._score(entries, {"A": 80})
        self.assertEqual(sc["selectors"]["rules"]["scored"], 1)
        self.assertEqual(sc["selectors"]["rules"]["correct"], 1)  # SELL, then fell

    def test_entry_without_date_skipped(self):
        e = _entry("A", "SELL", 100, 90, date=None)
        sc = self._score([e], {"A": 80})
        self.assertEqual(sc["selectors"], {})

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
