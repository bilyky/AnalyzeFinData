"""Tests for scripts/monitoring/invest_plan.py: step states and how fills cover steps, the
wait-a-day rule after a big market drop, the RSP-stretched swap, stops on fills, the
sell-window countdown, and "never act when not sure" on stale or missing prices.
All plan values are placeholders (2030 dates, round amounts), not a real plan."""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "monitoring"))
import invest_plan as ip  # noqa: E402

D = datetime.date
TODAY = D(2030, 3, 6)                       # a Wednesday; the last completed session is Tue 03-05


def _bars(closes, end=D(2030, 3, 5)):
    """Daily bars on consecutive sessions ending on `end`."""
    days, d = [], end
    while len(days) < len(closes):
        if ip.market_calendar.is_session(d):
            days.append(d.isoformat())
        d -= datetime.timedelta(days=1)
    days.reverse()
    return {"days": days, "idx": {x: i for i, x in enumerate(days)}, "c": list(closes), "o": list(closes),
            "h": list(closes), "l": list(closes), "v": [1.0] * len(closes)}


PLAN = {"steps": [{"date": "2030-03-06", "amount": 2000, "buy": "SPY"},
                  {"date": "2030-03-13", "amount": 2000, "buy": "RSP (SPY if RSP is stretched)"}],
        "rules": {"wait_if_market_day": -0.02, "index_stop": -0.08, "sell_from": "2030-04-15",
                  "sell_to": "2030-04-22", "latest_sell": "2030-04-29"}, "fills": []}
FLAT = _bars([100.0] * 80)


def _fill(date, amount, symbol="SPY", price=100.0):
    return {"date": date, "symbol": symbol, "shares": amount / price, "price": price}


class TestSteps(unittest.TestCase):
    def test_states(self):
        s = ip.step_status(PLAN, D(2030, 3, 13), [_fill("2030-03-06", 2000)])
        self.assertEqual([x["state"] for x in s], ["done", "due today"])
        self.assertEqual([x["state"] for x in ip.step_status(PLAN, D(2030, 3, 14), [])], ["overdue", "overdue"])
        self.assertEqual([x["state"] for x in ip.step_status(PLAN, D(2030, 3, 5), [])], ["later", "later"])

    def test_fills_cover_only_steps_on_or_before_their_date(self):
        early = [_fill("2030-03-01", 4000, symbol="XYZ")]                # before every step: covers nothing
        self.assertEqual([x["state"] for x in ip.step_status(PLAN, D(2030, 3, 14), early)], ["overdue", "overdue"])
        mid = [_fill("2030-03-10", 4000)]                                 # covers the first step only
        self.assertEqual([x["state"] for x in ip.step_status(PLAN, D(2030, 3, 14), mid)], ["done", "overdue"])

    def test_rounding_slack(self):
        self.assertEqual(ip.step_status(PLAN, TODAY, [_fill("2030-03-06", 1999.5)])[0]["state"], "done")
        self.assertEqual(ip.step_status(PLAN, TODAY, [_fill("2030-03-06", 1998.0)])[0]["state"], "due today")

    def test_step_on_a_market_holiday(self):
        plan = {**PLAN, "steps": [{"date": "2026-11-26", "amount": 1000, "buy": "SPY"}]}   # Thanksgiving
        r = ip.check(plan, D(2026, 11, 26), {"SPY": _bars([100.0] * 80, end=D(2026, 11, 25)), "RSP": None})
        self.assertEqual(r["actions"], ["Nothing to do today."])
        self.assertIn("falls on a market holiday", r["notes"][0])

    def test_due_step_buys(self):
        r = ip.check(PLAN, TODAY, {"SPY": FLAT, "RSP": FLAT})
        self.assertEqual(r["actions"], ["BUY step 2030-03-06: $2,000 SPY (limit order)"])

    def test_wait_a_day_after_a_big_market_drop(self):
        drop = _bars([100.0] * 79 + [97.9])                                   # -2.1%
        r = ip.check(PLAN, TODAY, {"SPY": drop, "RSP": FLAT})
        self.assertTrue(r["actions"][0].startswith("WAIT a day: SPY fell -2.1%"))
        plan = {**PLAN, "rules": {**PLAN["rules"], "wait_if_market_day": -0.25}}   # exact in floating point
        edge = _bars([100.0] * 79 + [75.0])                                   # exactly the threshold: wait
        self.assertTrue(ip.check(plan, TODAY, {"SPY": edge, "RSP": FLAT})["actions"][0].startswith("WAIT"))

    def test_rsp_step_when_rsp_is_stretched(self):
        hot = _bars([100.0] * 70 + [100.0 + k for k in range(1, 11)], end=D(2030, 3, 12))
        r = ip.check(PLAN, D(2030, 3, 13), {"SPY": _bars([100.0] * 80, end=D(2030, 3, 12)), "RSP": hot})
        due = [a for a in r["actions"] if "2030-03-13" in a][0]
        self.assertIn("is stretched (RSI 100", due)                          # the warning, not the step label


class TestFillsAndWindow(unittest.TestCase):
    def test_index_stop_and_winner_stop(self):
        fills = [{"date": "2030-03-06", "symbol": "SPY", "shares": 10, "price": 100.0},
                 {"date": "2030-03-13", "symbol": "ABC", "shares": 10, "price": 50.0, "stop": 45.0}]
        st = ip.fill_status(fills, {"SPY": 92.0, "ABC": 46.0}, -0.08)
        self.assertEqual([(f["stopped"], round(f["stop"], 2)) for f in st], [(True, 92.0), (False, 45.0)])
        self.assertAlmostEqual(st[0]["pnl"], -80.0)
        self.assertFalse(ip.fill_status(fills, {"SPY": 92.01, "ABC": 45.0}, -0.08)[0]["stopped"])
        self.assertTrue(ip.fill_status(fills, {"SPY": 99.0, "ABC": 45.0}, -0.08)[1]["stopped"])

    def test_sell_window(self):
        plan = {**PLAN, "fills": [_fill("2030-03-06", 100)]}
        r = ip.check(plan, D(2030, 4, 15), {"SPY": _bars([100.0] * 80, end=D(2030, 4, 12)), "RSP": FLAT})
        self.assertIn("SELL WINDOW: sell everything with limit orders by 2030-04-22 (latest 2030-04-29)",
                      r["actions"])
        r = ip.check(plan, D(2030, 4, 1), {"SPY": _bars([100.0] * 80, end=D(2030, 3, 29)), "RSP": FLAT})
        self.assertIn("14 days to the sell window", r["notes"][0])

    def test_sell_window_needs_holdings(self):
        r = ip.check(PLAN, D(2030, 4, 15), {"SPY": _bars([100.0] * 80, end=D(2030, 4, 12)), "RSP": FLAT})
        self.assertFalse([a for a in r["actions"] if "SELL WINDOW" in a])


class TestNotSure(unittest.TestCase):
    def test_stale_prices_turn_buys_and_sells_into_wait(self):
        stale = _bars([100.0] * 79 + [96.0], end=D(2030, 2, 26))           # last bar a week old
        plan = {**PLAN, "fills": [{"date": "2030-03-06", "symbol": "SPY", "shares": 10, "price": 105.0}]}
        r = ip.check(plan, TODAY, {"SPY": stale, "RSP": FLAT})
        self.assertIn("price cache is 5 session(s) old", r["doubts"][0])
        self.assertTrue(r["actions"])
        self.assertTrue(all(a.startswith("WAIT") for a in r["actions"]), r["actions"])

    def test_up_to_date_cache_has_no_doubt(self):
        self.assertEqual(ip.sessions_behind("2030-03-05", TODAY), 0)
        self.assertEqual(ip.sessions_behind("2030-03-06", TODAY), 0)       # after the close
        self.assertEqual(ip.sessions_behind("2030-03-01", TODAY), 2)       # Mon + Tue missing
        self.assertEqual(ip.sessions_behind("2026-11-25", D(2026, 11, 27)), 0)   # Thanksgiving in between

    def test_holding_without_bars_is_a_doubt(self):
        plan = {**PLAN, "fills": [{"date": "2030-03-06", "symbol": "ZZZ", "shares": 1, "price": 10.0, "stop": 9.0}]}
        r = ip.check(plan, TODAY, {"SPY": FLAT, "RSP": FLAT, "ZZZ": None})
        self.assertIn("no price bars for ZZZ", r["doubts"][0])
        self.assertTrue(all(a.startswith("WAIT") for a in r["actions"]), r["actions"])


if __name__ == "__main__":
    unittest.main()
