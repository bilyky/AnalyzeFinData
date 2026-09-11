"""Unit tests for scripts/backtesting/intc_options_replay_study.py.

Covers the study code the pure-pricer suite (test_option_pricing.py) does NOT: the per-leg and
per-strategy settlement math, the aggregation, the DTE-horizon guard, and the split-adjusting
loader. Offline, deterministic, stdlib unittest — no network and no live cache: the loader test
builds a tiny synthetic OHLCV file (with a 2:1 split discontinuity) in a temp dir; the math tests
construct Strategy/StrategyLeg objects directly.
"""
import datetime
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
import intc_options_replay_study as study          # noqa: E402
from aether import options_adviser as oa           # noqa: E402


def _leg(action, otype, strike, price):
    return oa.StrategyLeg(action=action, option_type=otype, strike=strike, price=price, contracts=1)


class TestLegOptionPnl(unittest.TestCase):
    """Per-share option P&L at expiry (credit to the holder)."""

    def test_long_call_itm(self):
        # bought a 100 call for 3; settles at 110 -> intrinsic 10 - 3 = +7
        self.assertAlmostEqual(study._leg_option_pnl(_leg("buy", "CALL", 100.0, 3.0), 110.0), 7.0)

    def test_long_call_otm_loses_premium(self):
        self.assertAlmostEqual(study._leg_option_pnl(_leg("buy", "CALL", 100.0, 3.0), 95.0), -3.0)

    def test_short_call_capped(self):
        # sold a 100 call for 3; settles 110 -> keep 3, pay 10 intrinsic = -7
        self.assertAlmostEqual(study._leg_option_pnl(_leg("sell", "CALL", 100.0, 3.0), 110.0), -7.0)

    def test_long_put_protects(self):
        self.assertAlmostEqual(study._leg_option_pnl(_leg("buy", "PUT", 100.0, 4.0), 90.0), 6.0)

    def test_short_put_keeps_premium(self):
        self.assertAlmostEqual(study._leg_option_pnl(_leg("sell", "PUT", 100.0, 4.0), 105.0), 4.0)


class TestSettle(unittest.TestCase):
    """Whole-position ($) P&L and the buy-hold baseline per structure (100-share lot)."""

    @staticmethod
    def _strat(kind, legs):
        return oa.Strategy(name=kind, kind=kind, legs=legs)

    def test_protective_put_floor(self):
        # stock 100->90 = -1000; long 95 put @2 -> (5-2)*100 = +300 ; baseline = the stock leg
        total, bh = study._settle(self._strat("protective_put", [_leg("buy", "PUT", 95.0, 2.0)]),
                                  100.0, 90.0)
        self.assertAlmostEqual(total, -700.0)
        self.assertAlmostEqual(bh, -1000.0)

    def test_covered_call_capped_winner(self):
        # stock 100->115 = +1500; short 110 call @2 -> (2-5)*100 = -300
        total, bh = study._settle(self._strat("covered_call", [_leg("sell", "CALL", 110.0, 2.0)]),
                                  100.0, 115.0)
        self.assertAlmostEqual(total, 1200.0)
        self.assertAlmostEqual(bh, 1500.0)

    def test_csp_has_no_stock_baseline(self):
        # cash-secured put holds NO stock -> baseline is None (not a buy-hold zero)
        total, bh = study._settle(self._strat("cash_secured_put", [_leg("sell", "PUT", 95.0, 3.0)]),
                                  100.0, 105.0)
        self.assertAlmostEqual(total, 300.0)     # OTM at expiry -> keep the whole premium
        self.assertIsNone(bh)


class TestExpiryIndex(unittest.TestCase):
    """The [DTE_MIN, DTE_MAX] settle-bar window guard."""

    def test_picks_first_bar_inside_window(self):
        # from 2020-01-02: floor +30 = 02-01, ceil +50 = 02-21; first bar >= floor is 02-05 (idx 2)
        dates = ["2020-01-02", "2020-01-15", "2020-02-05", "2020-02-20"]
        self.assertEqual(study._expiry_index(0, dates), 2)

    def test_gap_beyond_dte_max_returns_none(self):
        # the only forward bar is ~63 days out (> DTE_MAX 50) -> skip, don't mislabel as ~35-DTE
        self.assertIsNone(study._expiry_index(0, ["2020-01-02", "2020-03-05"]))

    def test_no_forward_bar_returns_none(self):
        self.assertIsNone(study._expiry_index(0, ["2020-01-02"]))


class TestAgg(unittest.TestCase):
    """Per-kind aggregation: win-rate, downside benefit, capped-winner cost, CSP baseline."""

    def test_covered_call_rollup(self):
        records = [
            # capped winner: s_t 115 > cap 110; buy-hold +1500, strat +1200 -> cap cost 300
            {"s_0": 100.0, "s_t": 115.0, "pnl": {"covered_call": 1200.0},
             "buyhold": {"covered_call": 1500.0}, "caps": {"covered_call": 110.0}},
            # down move: strat -800 beats buy-hold -1000 by +200 (the credit cushion)
            {"s_0": 100.0, "s_t": 90.0, "pnl": {"covered_call": -800.0},
             "buyhold": {"covered_call": -1000.0}, "caps": {"covered_call": 110.0}},
        ]
        a = study._agg(records, "covered_call")
        self.assertEqual(a["n"], 2)
        self.assertEqual(a["win_rate"], 0.5)                 # +1200 win, -800 loss
        self.assertEqual(a["down_moves"], 1)
        self.assertEqual(a["protection_type"], "premium cushion")
        self.assertEqual(a["beat_bh_down_n"], 1)
        self.assertAlmostEqual(a["mean_downside_benefit"], 200.0)
        self.assertEqual(a["capped_winner_n"], 1)
        self.assertAlmostEqual(a["mean_cap_opportunity_cost"], 300.0)

    def test_csp_has_no_buyhold_or_cap(self):
        records = [{"s_0": 100.0, "s_t": 105.0, "pnl": {"cash_secured_put": 300.0},
                    "buyhold": {"cash_secured_put": None}, "caps": {"cash_secured_put": None}}]
        a = study._agg(records, "cash_secured_put")
        self.assertEqual(a["n"], 1)
        self.assertIsNone(a["mean_vs_buyhold"])              # no stock baseline to diff against
        self.assertIsNone(a["protection_type"])             # CSP protects nothing on the downside
        self.assertEqual(a["capped_winner_n"], 0)           # no short call = no cap

    def test_absent_kind_returns_none(self):
        self.assertIsNone(study._agg([], "collar"))


class TestLoadSplitAdjust(unittest.TestCase):
    """_load must skip provisional bars AND back-adjust a raw split onto one price scale."""

    def _write_cache(self, bars):
        # bars: list of (date, open, high, low, close, extra_dict)
        ts = {}
        for d, o, h, l, c, extra in bars:
            row = {"1. open": str(o), "2. high": str(h), "3. low": str(l), "4. close": str(c),
                   "5. volume": "1000"}
            row.update(extra)
            ts[d] = row
        fd, path = tempfile.mkstemp(suffix="_daily.json")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"Time Series (Daily)": ts}, f)
        self.addCleanup(os.remove, path)
        return path

    def test_split_backadjusted_and_provisional_skipped(self):
        # Need > WARMUP+5 (=31) bars. Build 20 pre-split @130, a 2:1 split bar @65, 19 post @65,
        # plus one provisional flat bar that must be dropped.
        bars = []
        for k in range(20):                                  # pre-split ~130
            bars.append((f"2000-06-{k+1:02d}", 130, 131, 129, 130, {}))
        bars.append(("2000-07-31", 65, 66, 64, 65, {}))      # 2:1 split (close 130->65, open agrees)
        for k in range(19):                                  # post-split ~65
            bars.append((f"2000-08-{k+1:02d}", 65, 66, 64, 65, {}))
        bars.append(("2000-09-01", 65, 65, 65, 65, {"provisional": True}))   # must be skipped
        path = self._write_cache(bars)

        data = study._load(path)
        self.assertIsNotNone(data)
        dates, highs, lows, closes = data
        self.assertEqual(len(closes), 40)                    # 20 + 1 + 19, provisional dropped
        self.assertNotIn("2000-09-01", dates)                # provisional bar gone
        # After back-adjust the pre-split closes are rescaled onto the modern (~65) scale, so no
        # adjacent close ratio remains near the 0.5 split cliff.
        self.assertLess(closes[0], 90.0)                     # ~65, not the raw ~130
        for a, b in zip(closes, closes[1:]):
            self.assertGreater(b / a, 0.6, "a phantom split cliff survived adjustment")


if __name__ == "__main__":
    unittest.main()
