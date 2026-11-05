"""Tests for scripts/monitoring/invest_plan.py: step states, the wait-a-day rule after a big
market drop, the RSP-stretched swap, stops on fills, and the sell-window countdown."""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "monitoring"))
import invest_plan as ip  # noqa: E402

D = datetime.date


def _bars(closes):
    days = [(D(2026, 8, 1) + datetime.timedelta(days=k)).isoformat() for k in range(len(closes))]
    return {"days": days, "idx": {d: i for i, d in enumerate(days)}, "c": list(closes), "o": list(closes),
            "h": list(closes), "l": list(closes), "v": [1.0] * len(closes)}


PLAN = {"steps": [{"date": "2026-11-06", "amount": 5000, "buy": "SPY"},
                  {"date": "2026-11-12", "amount": 5000, "buy": "RSP (SPY if RSP is stretched)"}],
        "rules": {"wait_if_market_day": -0.02, "index_stop": -0.08, "sell_from": "2026-12-15",
                  "sell_to": "2026-12-22", "latest_sell": "2026-12-28"}, "fills": []}
FLAT = _bars([100.0] * 80)


class TestSteps(unittest.TestCase):
    def test_states(self):
        s = ip.step_status(PLAN, D(2026, 11, 12), [{"shares": 50, "price": 100.0}])   # $5000 filled
        self.assertEqual([x["state"] for x in s], ["done", "due today"])
        s = ip.step_status(PLAN, D(2026, 11, 13), [])
        self.assertEqual([x["state"] for x in s], ["overdue", "overdue"])
        self.assertEqual([x["state"] for x in ip.step_status(PLAN, D(2026, 11, 5), [])], ["later", "later"])

    def test_due_step_buys(self):
        r = ip.check(PLAN, D(2026, 11, 6), {"SPY": FLAT, "RSP": FLAT})
        self.assertEqual(r["actions"], ["BUY step 2026-11-06: $5,000 SPY (limit order)"])

    def test_wait_a_day_after_a_big_market_drop(self):
        drop = _bars([100.0] * 79 + [97.9])                                   # -2.1%
        r = ip.check(PLAN, D(2026, 11, 6), {"SPY": drop, "RSP": FLAT})
        self.assertTrue(r["actions"][0].startswith("WAIT a day: SPY fell -2.1%"))
        plan = {**PLAN, "rules": {**PLAN["rules"], "wait_if_market_day": -0.25}}   # exact in floating point
        edge = _bars([100.0] * 79 + [75.0])                                   # exactly the threshold: wait
        self.assertTrue(ip.check(plan, D(2026, 11, 6), {"SPY": edge, "RSP": FLAT})["actions"][0].startswith("WAIT"))

    def test_rsp_step_when_rsp_is_stretched(self):
        hot = _bars([100.0] * 70 + [100.0 + k for k in range(1, 11)])          # +10% vs the 50-day
        r = ip.check(PLAN, D(2026, 11, 12), {"SPY": FLAT, "RSP": hot})
        due = [a for a in r["actions"] if "2026-11-12" in a][0]
        self.assertIn("is stretched (RSI 100", due)                          # the warning, not the step label


class TestFillsAndWindow(unittest.TestCase):
    def test_index_stop_and_winner_stop(self):
        fills = [{"date": "2026-11-06", "symbol": "SPY", "shares": 10, "price": 100.0},
                 {"date": "2026-11-19", "symbol": "ABC", "shares": 10, "price": 50.0, "stop": 45.0}]
        st = ip.fill_status(fills, {"SPY": 92.0, "ABC": 46.0}, -0.08)
        self.assertEqual([(f["stopped"], round(f["stop"], 2)) for f in st], [(True, 92.0), (False, 45.0)])
        self.assertAlmostEqual(st[0]["pnl"], -80.0)
        self.assertFalse(ip.fill_status(fills, {"SPY": 92.01, "ABC": 45.0}, -0.08)[0]["stopped"])
        self.assertTrue(ip.fill_status(fills, {"SPY": 99.0, "ABC": 45.0}, -0.08)[1]["stopped"])

    def test_sell_window(self):
        plan = {**PLAN, "fills": [{"date": "2026-11-06", "symbol": "SPY", "shares": 1, "price": 100.0}]}
        r = ip.check(plan, D(2026, 12, 15), {"SPY": FLAT, "RSP": FLAT})
        self.assertIn("SELL WINDOW: sell everything with limit orders by 2026-12-22 (latest 2026-12-28)", r["actions"])
        r = ip.check(plan, D(2026, 12, 1), {"SPY": FLAT, "RSP": FLAT})
        self.assertIn("14 days to the sell window", r["notes"][0])


if __name__ == "__main__":
    unittest.main()
