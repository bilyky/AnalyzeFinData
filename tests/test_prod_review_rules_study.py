"""Tests for scripts/backtesting/prod_review_rules_study.py and the pieces it added:
the live breadth helpers, the backtest's profit-lock rule and profile_fn, the waiver masks,
the verdicts and the scale-in ledger reader."""
import os
import sys
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
import ai_portfolio_game as game
import portfolio_backtest as pb
import prod_review_rules_study as st
import rule_walkforward as wf
from tests.test_portfolio_backtest import DAYS, _bars


class TestLiveBreadthHelpers(unittest.TestCase):
    def test_breadth_downgrade(self):
        for base, spy, rsp, want in [
            ("AGGRESSIVE", 10.0, 2.5, "BALANCED"),    # PROD 10-13: divergence 7.5
            ("BALANCED", 10.0, 2.5, "DEFENSIVE"),
            ("DEFENSIVE", 10.0, 2.5, "DEFENSIVE"),
            ("BALANCED", 6.5, 2.5, "BALANCED"),       # exactly 4.0: not more than the limit
            ("BALANCED", 10.0, None, "BALANCED"),     # missing data never downgrades
        ]:
            with self.subTest(base=base, spy=spy, rsp=rsp):
                self.assertEqual(game.breadth_downgrade(base, spy, rsp), want)

    def test_trend_score_from_closes_matches_the_live_symbol_score(self):
        closes = [100 + 0.1 * k for k in range(250)]
        with mock.patch.object(game, "_load_closes", return_value=closes):
            self.assertEqual(game.calculate_ticker_trend_score("SPY"), game.trend_score_from_closes(closes))
        self.assertEqual(game.trend_score_from_closes(closes), 10.0)
        self.assertIsNone(game.trend_score_from_closes(closes[:199]))

    def test_get_market_regime_still_downgrades(self):
        with mock.patch.object(game.openpyxl, "load_workbook", side_effect=OSError("no book")), \
             mock.patch.object(game, "calculate_ticker_trend_score", side_effect=lambda s: 10.0 if s == "SPY" else 2.5):
            self.assertEqual(game.get_market_regime(), "DEFENSIVE")   # BALANCED fallback, then breadth

    def test_live_profiles_uses_data_up_to_each_day(self):
        days = [f"2025-{m:02d}-{d:02d}" for m in range(1, 13) for d in (1, 15)]
        n = 400
        ts_days = [f"2024-{(k // 28) % 12 + 1:02d}-{k % 28 + 1:02d}" for k in range(n)]
        ts_days = sorted(set(ts_days))
        up = {d: {"4. close": str(100 + k)} for k, d in enumerate(ts_days)}
        down = {d: {"4. close": str(500 - k)} for k, d in enumerate(ts_days)}
        spy_l60 = {d: [0, 3.0] for d in days}                        # AGGRESSIVE band
        prof = st.live_profiles(days, spy_l60, up, down)             # SPY +10, RSP -10
        self.assertTrue(all(p == "BALANCED" for p in prof.values()))
        self.assertTrue(all(p == "AGGRESSIVE" for p in st.live_profiles(days, spy_l60, up, up).values()))
        flat = {d: [0, 0.0] for d in days}                           # BALANCED band
        self.assertTrue(all(p == "BALANCED" for p in st.live_profiles(days, flat, up, up).values()))
        self.assertTrue(all(p == "DEFENSIVE" for p in st.live_profiles(days, flat, up, down).values()))
        self.assertEqual(st.profile_changes({"a": "X", "b": "Y", "c": "Y", "d": "X"}), 2)


class TestProfitLock(unittest.TestCase):
    """Entry day 1 at 10 (ATR 1, BALANCED x2.5). Day 2 closes at 14: safely in profit, so the stop
    ratchets to 14 - k. With a DEFENSIVE day 2, 'day' uses k=1.5 (stop 12.5) and day 3's low of 12
    sells; 'entry' keeps k=2.5 (stop 11.5) and holds."""

    def _run(self, lock, profile_fn=None):
        bars = {"A": _bars([10, 10, 14, 13], lows=[10, 10, 14, 12]), "SPY": _bars([100] * 4)}
        panel = {"A": {DAYS[0]: [6.0, 0.0]}}
        prof = {DAYS[0]: "BALANCED", DAYS[1]: "BALANCED", DAYS[2]: "DEFENSIVE", DAYS[3]: "DEFENSIVE"}
        return pb.simulate(panel, bars, DAYS[:4], cost_bps=0.0, exit_rules={"lock": lock} if lock else None,
                           profile_fn=(profile_fn or prof.get))

    def test_day_multiple_tightens_on_a_defensive_day(self):
        r = self._run("day")
        self.assertEqual([(t["exit_date"], t["exit"], t["reason"]) for t in r["trades"]], [(DAYS[3], 12.5, "stop")])

    def test_entry_multiple_keeps_the_wider_trail(self):
        r = self._run("entry")
        self.assertEqual((r["trades"], r["open_positions"]), ([], ["A"]))

    def test_no_lock_keeps_the_initial_stop(self):
        self.assertEqual(self._run(None)["open_positions"], ["A"])

    def test_breakeven_lock_lifts_the_stop_to_entry(self):
        # Close 11.6: in profit by 1.6 ATR -> trail 11.6 - 2.5 = 9.1, breakeven lifts it to 10;
        # day 3's low of 9.5 then sells at 10 (the trail alone would hold).
        bars = {"A": _bars([10, 10, 11.6, 11], lows=[10, 10, 11.6, 9.5]), "SPY": _bars([100] * 4)}
        panel = {"A": {DAYS[0]: [6.0, 0.0]}}
        r = pb.simulate(panel, bars, DAYS[:4], cost_bps=0.0, exit_rules={"lock": "entry"},
                        profile_fn=lambda d: "BALANCED")
        self.assertEqual([(t["exit"], t["reason"]) for t in r["trades"]], [(10.0, "stop")])

    def test_profile_fn_sets_the_entry_rules(self):
        # Score 6 passes BALANCED's threshold 5, not DEFENSIVE's 10.
        bars = {"A": _bars([10] * 3), "SPY": _bars([100] * 3)}
        panel = {"A": {DAYS[0]: [6.0, 0.0]}}
        self.assertEqual(pb.simulate(panel, bars, DAYS[:3], cost_bps=0.0, profile_fn=lambda d: "DEFENSIVE")["open_positions"], [])
        self.assertEqual(pb.simulate(panel, bars, DAYS[:3], cost_bps=0.0, profile_fn=lambda d: "BALANCED")["open_positions"], ["A"])


def _arrays(rows):
    """rule_walkforward arrays from (s10, l60, rr, gain) rows that pass every other live gate."""
    n = len(rows)
    a = {"day": np.array(["2025-01-02"] * n), "sym": np.array([f"S{k}" for k in range(n)]),
         "s10": np.array([r[0] for r in rows], dtype=float), "l60": np.array([r[1] for r in rows], dtype=float),
         "rr": np.array([r[2] for r in rows], dtype=float), "gain": np.array([r[3] for r in rows], dtype=float),
         "setup_full": np.ones(n, bool), "above": np.ones(n, bool), "up3": np.ones(n, bool),
         "leader": np.zeros(n, bool), "mf_weak": np.zeros(n, bool), "past60": np.zeros(n)}
    a["s10w"] = a["s10"]
    return a


class TestWaiverMasks(unittest.TestCase):
    ROWS = [(3.0, 3.0, 2.5, 8.0),    # normal pass
            (3.0, 3.0, 0.3, 1.0),    # not elite, low R/R: rejected by all
            (5.0, 6.0, 0.08, 0.95),  # elite, R/R 0.08 (COMP): current only
            (5.0, 6.0, 0.6, 2.0)]    # elite, R/R 0.6: current and waiver_floor

    def test_masks(self):
        a = _arrays(self.ROWS)
        m = st.rr_masks(a)
        self.assertEqual(m["current"].tolist(), [True, False, True, True])
        self.assertEqual(m["waiver_off"].tolist(), [True, False, False, False])
        self.assertEqual(m["waiver_floor"].tolist(), [True, False, False, True])

    def test_current_equals_the_walkforward_current_rule(self):
        a = _arrays(self.ROWS)
        self.assertTrue(np.array_equal(st.rr_masks(a)["current"], wf.admit_mask(a, wf.CURRENT)))


class TestVerdicts(unittest.TestCase):
    def T(self, m, t):
        return {"mean_daily_pct": m, "t_hac": t}

    def test_waiver(self):
        dd = {"current": -10.0, "waiver_off": -12.0, "waiver_floor": -16.0}
        for off, floor, want in [
            (self.T(0.02, 2.5), self.T(0.0, 0.0), "TIGHTEN:waiver_off"),
            (self.T(0.02, 1.5), self.T(0.0, 0.0), "INCONCLUSIVE"),      # positive but t too small
            (self.T(0.0, 0.0), self.T(0.02, 3.0), "INCONCLUSIVE"),      # floor passes t but dd 6 pts deeper
            (self.T(-0.02, -2.5), self.T(0.0, 0.0), "WAIVER_HELPS"),
            (self.T(-0.02, -1.0), self.T(0.0, 0.0), "INCONCLUSIVE"),
        ]:
            with self.subTest(off=off, floor=floor):
                self.assertEqual(st.waiver_verdict({"waiver_off": off, "waiver_floor": floor}, dd), want)

    def test_lock(self):
        self.assertEqual(st.lock_verdict(self.T(0.01, 2.0), -10.0, -9.0), "ENTRY_MULT")
        self.assertEqual(st.lock_verdict(self.T(0.01, 2.0), -15.0, -9.0), "INCONCLUSIVE")   # 6 pts deeper
        self.assertEqual(st.lock_verdict(self.T(-0.01, -2.0), -10.0, -9.0), "DAY_MULT")
        self.assertEqual(st.lock_verdict(self.T(None, None), -10.0, -9.0), "INCONCLUSIVE")


class TestScaleInLedger(unittest.TestCase):
    HISTORY = [
        {"date": "2026-10-20", "type": "SELL", "symbol": "MSCI", "price": 591.63, "details": "Scale-out (Bank-As-You-Go): bank 60%"},
        {"date": "2026-10-22", "type": "BUY_SCALE_IN", "symbol": "MSCI", "price": 610.6225},
        {"date": "2026-09-01", "type": "BUY_SCALE_IN", "symbol": "NVDA", "price": 100.0},
        {"date": "2026-09-20", "type": "SELL", "symbol": "NVDA", "price": 110.0, "details": "Exit: stop breached"},
        {"date": "2026-08-01", "type": "SELL", "symbol": "OLD", "price": 50.0, "details": "Scale-out (Bank-As-You-Go)"},
        {"date": "2026-09-01", "type": "BUY_SCALE_IN", "symbol": "OLD", "price": 60.0},   # 31 days later
    ]

    def test_rows_and_flags(self):
        out = st.ledger_scale_ins(self.HISTORY, lambda sym, day: {"MSCI": 650.0, "OLD": 54.0}.get(sym))
        rows = {r["symbol"]: r for r in out["rows"]}
        self.assertTrue(rows["MSCI"]["after_scale_out"])
        self.assertEqual(rows["MSCI"]["sold_at"], 591.63)
        self.assertEqual(rows["MSCI"]["vs_sale_pct"], 3.21)
        self.assertEqual(rows["MSCI"]["ret_pct"], 6.45)            # marked at the latest close
        self.assertFalse(rows["NVDA"]["after_scale_out"])
        self.assertEqual(rows["NVDA"]["ret_pct"], 10.0)            # marked at the next full exit
        self.assertFalse(rows["OLD"]["after_scale_out"])           # outside CHURN_DAYS
        self.assertEqual(out["direction"], "TOO_FEW")

    def test_latest_close_must_postdate_the_add(self):
        ts = {"2026-10-30": {"4. close": "50.0"}}
        with mock.patch.object(st.br, "load_ohlcv", return_value=ts):
            last_close = st.latest_close_fn()
            self.assertEqual(last_close("MNST", "2026-10-29"), 50.0)
            self.assertIsNone(last_close("MNST", "2026-11-02"))   # cache ends before the add

    def test_direction(self):
        hist = []
        for k in range(3):
            hist += [{"date": f"2026-10-0{k + 1}", "type": "SELL", "symbol": f"C{k}", "price": 10.0, "details": "Scale-out"},
                     {"date": f"2026-10-0{k + 2}", "type": "BUY_SCALE_IN", "symbol": f"C{k}", "price": 11.0},
                     {"date": f"2026-10-0{k + 2}", "type": "BUY_SCALE_IN", "symbol": f"O{k}", "price": 10.0}]
        closes = {**{f"C{k}": 10.0 for k in range(3)}, **{f"O{k}": 12.0 for k in range(3)}}
        out = st.ledger_scale_ins(hist, lambda s, d: closes[s])
        self.assertEqual(out["direction"], "CHURN_ADDS_WORSE")
        closes = {**{f"C{k}": 13.2 for k in range(3)}, **{f"O{k}": 12.0 for k in range(3)}}   # both +20%
        self.assertEqual(st.ledger_scale_ins(hist, lambda s, d: closes[s])["direction"], "NO_CLEAR_DIFFERENCE")


if __name__ == "__main__":
    unittest.main()
