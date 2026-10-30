"""Tests for scripts/backtesting/portfolio_backtest.py — the standalone portfolio replay.

Pins (1) parity of the mirrored rule tables with the live game functions, so the
backtest cannot silently drift from production rules, and (2) the simulator's
execution contract on synthetic bars: next-open fills (no look-ahead), intraday ATR
stop at min(open, stop), the regime cash buffer, and the metric arithmetic.
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
import ai_portfolio_game as game
import portfolio_backtest as pb

DAYS = [f"2026-01-{d:02d}" for d in range(1, 21)]


def _bars(opens, highs=None, lows=None, closes=None, atr=1.0):
    n = len(opens)
    return {"idx": {d: i for i, d in enumerate(DAYS[:n])}, "o": list(opens),
            "h": list(highs or opens), "l": list(lows or opens), "c": list(closes or opens),
            "atr": [atr] * n, "sma50": [None] * n}


class TestRuleParity(unittest.TestCase):
    """The mirrored tables must equal what the live game computes for each regime."""

    def test_profile_rules_match_get_strategy_rules(self):
        for profile, rules in pb.PROFILE_RULES.items():
            # profile == regime in the backtest: AGGRESSIVE is the bullish case.
            regime = "AGGRESSIVE" if profile == "AGGRESSIVE" else "BALANCED"
            with mock.patch("ai_portfolio_game.get_market_regime", return_value=regime):
                live = game.get_strategy_rules(profile)
            for key, val in rules.items():
                self.assertEqual(val, live[key], f"{profile}.{key}")

    def test_slot_expansion_matches_determine_max_positions(self):
        for cash_ratio in (0.0, 0.10, 0.15, 0.16, 0.5, 0.9):
            for n in range(0, 9):
                for base in (3, 5, 6):
                    self.assertEqual(pb.expanded_max_positions(cash_ratio, n, base),
                                     game.determine_max_positions(cash_ratio, n, base))

    def test_regime_band(self):
        self.assertEqual(pb.profile_for(2.1), "AGGRESSIVE")
        self.assertEqual(pb.profile_for(2.0), "BALANCED")
        self.assertEqual(pb.profile_for(-2.0), "BALANCED")
        self.assertEqual(pb.profile_for(-2.1), "DEFENSIVE")
        self.assertEqual(pb.profile_for(None), "BALANCED")


class TestSimulate(unittest.TestCase):
    def test_fills_at_next_open_no_lookahead(self):
        bars = {"A": _bars([10, 12, 12, 12, 12], closes=[11, 12, 12, 12, 12]),
                "SPY": _bars([100] * 5)}
        panel = {"A": {DAYS[0]: [6.0, 0.0]}, "SPY": {DAYS[0]: [0.0, 0.0]}}
        r = pb.simulate(panel, bars, DAYS[:5], risk_rules=True, cost_bps=0.0, initial=10_000.0)
        self.assertEqual(r["open_positions"], ["A"])
        # Day-0 score -> bought at day-1 OPEN (12), never at day-0's prices.
        self.assertEqual(r["curve"][0][2], 0.0)
        self.assertAlmostEqual(r["curve"][1][2], 0.15 * 10_000.0)   # BALANCED 15% cap

    def test_below_threshold_never_buys(self):
        bars = {"A": _bars([10] * 4), "SPY": _bars([100] * 4)}
        panel = {"A": {d: [4.9, 0.0] for d in DAYS[:4]}}            # BALANCED needs >= 5.0
        r = pb.simulate(panel, bars, DAYS[:4], risk_rules=True, cost_bps=0.0)
        self.assertEqual(r["open_positions"], [])
        self.assertEqual(r["trades"], [])

    def test_intraday_stop_fills_at_min_open_stop(self):
        # Entry at day-1 open 10 with ATR 1 x 2.5 (BALANCED) -> stop 7.5.
        bars = {"A": _bars([10, 10, 9, 6], lows=[10, 10, 7.0, 5.0]), "SPY": _bars([100] * 4)}
        panel = {"A": {DAYS[0]: [6.0, 0.0]}}
        r = pb.simulate(panel, bars, DAYS[:4], risk_rules=True, cost_bps=0.0)
        self.assertEqual(len(r["trades"]), 1)
        t = r["trades"][0]
        self.assertEqual((t["reason"], t["exit_date"], t["exit"]), ("stop", DAYS[2], 7.5))
        # Gap through the stop fills at the (lower) open, not the stop.
        bars["A"] = _bars([10, 10, 6, 6], lows=[10, 10, 5.0, 5.0])
        t = pb.simulate(panel, bars, DAYS[:4], risk_rules=True, cost_bps=0.0)["trades"][0]
        self.assertEqual(t["exit"], 6)

    def test_signal_only_has_no_stop(self):
        bars = {"A": _bars([10, 10, 6, 6], lows=[10, 10, 5.0, 5.0]), "SPY": _bars([100] * 4)}
        panel = {"A": {DAYS[0]: [6.0, 0.0]}}
        r = pb.simulate(panel, bars, DAYS[:4], risk_rules=False, cost_bps=0.0)
        self.assertEqual(r["trades"], [])
        self.assertEqual(r["open_positions"], ["A"])

    def test_soft_exit_sells_next_open(self):
        bars = {"A": _bars([10, 10, 9, 8]), "SPY": _bars([100] * 4)}
        panel = {"A": {DAYS[0]: [6.0, 0.0], DAYS[1]: [-1.0, -1.0]}}  # S10+L60 < 0, at a loss
        t = pb.simulate(panel, bars, DAYS[:4], risk_rules=True, cost_bps=0.0)["trades"][0]
        self.assertEqual((t["reason"], t["exit_date"], t["exit"]), ("momentum decay", DAYS[2], 9))

    def test_defensive_regime_keeps_half_cash(self):
        syms = [f"S{k}" for k in range(8)]
        bars = {s: _bars([10] * 3) for s in syms} | {"SPY": _bars([100] * 3)}
        panel = {s: {DAYS[0]: [12.0, 0.0]} for s in syms}
        panel["SPY"] = {DAYS[0]: [0.0, -3.0]}                        # SPY L60 < -2
        r = pb.simulate(panel, bars, DAYS[:3], risk_rules=True, cost_bps=0.0, initial=10_000.0)
        invested = r["curve"][1][2]
        self.assertLessEqual(invested, 0.5 * 10_000.0 + 1e-6)
        self.assertAlmostEqual(invested, 0.10 * 10_000.0 * len(r["open_positions"]))

    def test_trade_records_stop_and_loss_as_pct_of_equity(self):
        # Capped to 40 shares (stop 7.5); the stop fills at 7.5: a loss of exactly 1% of 10k.
        bars = {"A": _bars([10, 10, 7.5, 7.5], lows=[10, 10, 7.0, 7.0]), "SPY": _bars([100] * 4)}
        panel = {"A": {DAYS[0]: [6.0, 0.0]}}
        t = pb.simulate(panel, bars, DAYS[:4], cost_bps=0.0, initial=10_000.0,
                        max_trade_risk_pct=0.01)["trades"][0]
        self.assertEqual((t["reason"], t["stop"], t["exit"]), ("stop", 7.5, 7.5))
        self.assertAlmostEqual(t["pnl_pct_equity"], -1.0)

    def test_loss_is_measured_on_the_equity_the_buy_was_sized_on(self):
        # A lifts equity to 12,000 before B's buy: B is capped at 120 / 2.5 = 48 shares, and
        # its stop-out (48 x 2.5 = $120) is 1% of 12,000, not 1.2% of the starting 10,000.
        bars = {"A": _bars([10, 10, 60, 60, 60], closes=[10, 60, 60, 60, 60]),
                "B": _bars([10, 10, 10, 7.5, 7.5], lows=[10, 10, 10, 7.0, 7.0]),
                "SPY": _bars([100] * 5)}
        panel = {"A": {DAYS[0]: [6.0, 0.0]}, "B": {DAYS[1]: [6.0, 0.0]}}
        r = pb.simulate(panel, bars, DAYS[:5], cost_bps=0.0, initial=10_000.0, max_trade_risk_pct=0.01)
        t = next(t for t in r["trades"] if t["symbol"] == "B")
        self.assertEqual(t["reason"], "stop")
        self.assertAlmostEqual(t["pnl_pct_equity"], -1.0)

    def test_loss_includes_costs(self):
        bars = {"A": _bars([10, 10, 7.5, 7.5], lows=[10, 10, 7.0, 7.0]), "SPY": _bars([100] * 4)}
        panel = {"A": {DAYS[0]: [6.0, 0.0]}}
        t = pb.simulate(panel, bars, DAYS[:4], cost_bps=10.0, initial=10_000.0,
                        max_trade_risk_pct=0.01)["trades"][0]
        qty = 400.0 / (10 * 1.001)                            # the capped $400 buys at open + cost
        self.assertAlmostEqual(t["pnl_pct_equity"], qty * (7.5 * 0.999 - 10 * 1.001) / 100, places=3)

    def test_gap_through_the_stop_loses_more_than_the_cap(self):
        # Same buy, but day 2 opens at 6, below the 7.5 stop: 40 x (10 - 6) = $160 = 1.6%.
        bars = {"A": _bars([10, 10, 6, 6], lows=[10, 10, 5.0, 5.0]), "SPY": _bars([100] * 4)}
        panel = {"A": {DAYS[0]: [6.0, 0.0]}}
        r = pb.simulate(panel, bars, DAYS[:4], cost_bps=0.0, initial=10_000.0, max_trade_risk_pct=0.01)
        self.assertAlmostEqual(r["trades"][0]["pnl_pct_equity"], -1.6)
        tail = pb.stop_tail(r["trades"], 0.01)
        self.assertEqual((tail["n_stop_exits"], tail["n_gap_through"]), (1, 1))
        self.assertAlmostEqual(tail["gap_median_pct_below_stop"], 20.0)
        self.assertEqual(tail["share_over_cap"], 1.0)
        self.assertTrue(tail["gate_fail"])                  # P95 1.6% > 1.5%

    def test_loss_cap_trims_buy_to_pct_of_equity(self):
        # Entry at open 10, stop 10 - 2.5 x ATR 1 = 7.5: a 1% cap on 10k equity allows
        # 100 / 2.5 = 40 shares = $400, not the 15% ($1,500) allocation.
        bars = {"A": _bars([10, 10, 7.5, 7.5], lows=[10, 10, 7.0, 7.0]), "SPY": _bars([100] * 4)}
        panel = {"A": {DAYS[0]: [6.0, 0.0]}}
        r = pb.simulate(panel, bars, DAYS[:4], risk_rules=True, cost_bps=0.0, initial=10_000.0,
                        max_trade_risk_pct=0.01)
        self.assertAlmostEqual(r["curve"][1][2], 400.0)
        self.assertEqual(r["cap_trims"], 1)
        # The stop-out loses exactly the cap.
        self.assertEqual(r["trades"][0]["reason"], "stop")
        self.assertAlmostEqual(r["curve"][-1][1], 10_000.0 - 100.0)
        uncapped = pb.simulate(panel, bars, DAYS[:4], risk_rules=True, cost_bps=0.0, initial=10_000.0)
        self.assertAlmostEqual(uncapped["curve"][-1][1], 10_000.0 - 375.0)
        self.assertEqual(uncapped["cap_trims"], 0)

    def test_admit_hook_blocks_a_buy_the_score_allows(self):
        bars = {"A": _bars([10] * 3), "SPY": _bars([100] * 3)}
        panel = {"A": {DAYS[0]: [6.0, 0.0]}}
        seen = []
        blocked = pb.simulate(panel, bars, DAYS[:3], cost_bps=0.0, initial=10_000.0,
                              admit=lambda sym, day: seen.append((sym, day)) or False)
        self.assertEqual(blocked["curve"][1][2], 0.0)          # nothing bought
        self.assertIn(("A", DAYS[0]), seen)
        allowed = pb.simulate(panel, bars, DAYS[:3], cost_bps=0.0, initial=10_000.0, admit=lambda s, d: True)
        self.assertGreater(allowed["curve"][1][2], 0.0)

    def test_rank_hook_decides_which_name_takes_the_last_slot(self):
        bars = {"A": _bars([10] * 3), "B": _bars([10] * 3), "SPY": _bars([100] * 3)}
        panel = {"A": {DAYS[0]: [6.0, 0.0]}, "B": {DAYS[0]: [5.5, 0.0]}}
        one_slot = {k: dict(v, max_positions=1) for k, v in pb.PROFILE_RULES.items()}
        with mock.patch.object(pb, "PROFILE_RULES", one_slot), \
             mock.patch.object(pb, "expanded_max_positions", side_effect=lambda c, n, base: base):
            default = pb.simulate(panel, bars, DAYS[:3], cost_bps=0.0, initial=10_000.0)
            flipped = pb.simulate(panel, bars, DAYS[:3], cost_bps=0.0, initial=10_000.0,
                                  rank=lambda sym, day, sc: -sc[0])
        self.assertEqual(default["open_positions"], ["A"])     # higher S10 + L60 first
        self.assertEqual(flipped["open_positions"], ["B"])

    def test_loss_cap_not_binding_keeps_allocation(self):
        # Stop gap 2.5 on a $1,500 buy risks $375 <= 5% of 10k: no trim.
        bars = {"A": _bars([10] * 3), "SPY": _bars([100] * 3)}
        panel = {"A": {DAYS[0]: [6.0, 0.0]}}
        r = pb.simulate(panel, bars, DAYS[:3], risk_rules=True, cost_bps=0.0, initial=10_000.0,
                        max_trade_risk_pct=0.05)
        self.assertAlmostEqual(r["curve"][1][2], 1_500.0)
        self.assertEqual(r["cap_trims"], 0)

    def test_loss_cap_ignored_without_risk_rules(self):
        bars = {"A": _bars([10] * 3), "SPY": _bars([100] * 3)}
        panel = {"A": {DAYS[0]: [6.0, 0.0]}}
        r = pb.simulate(panel, bars, DAYS[:3], risk_rules=False, cost_bps=0.0, initial=10_000.0,
                        max_trade_risk_pct=0.01)
        self.assertAlmostEqual(r["curve"][1][2], 10_000.0 / 5)
        self.assertEqual(r["cap_trims"], 0)

    def test_loss_cap_sized_on_current_equity(self):
        # A is bought at 10 (cap: 1% of 10k / 2.5 stop gap = 40 shares) and closes at 60, so
        # equity is 9,600 + 40 x 60 = 12,000 when B's buy is sized: cap 120 / 2.5 x 10 = $480.
        bars = {"A": _bars([10, 10, 60, 60], closes=[10, 60, 60, 60]),
                "B": _bars([10] * 4), "SPY": _bars([100] * 4)}
        panel = {"A": {DAYS[0]: [6.0, 0.0]}, "B": {DAYS[1]: [6.0, 0.0]}}
        r = pb.simulate(panel, bars, DAYS[:4], risk_rules=True, cost_bps=0.0, initial=10_000.0,
                        max_trade_risk_pct=0.01)
        self.assertAlmostEqual(r["curve"][2][2], 40 * 60 + 480.0)
        self.assertEqual(r["cap_trims"], 2)

    def test_disable_rejects_unknown_rule(self):
        with self.assertRaises(ValueError):
            pb.simulate({}, {"SPY": _bars([100] * 2)}, DAYS[:2], disable={"nope"})

    def test_disable_stop_keeps_position(self):
        bars = {"A": _bars([10, 10, 6, 6], lows=[10, 10, 5.0, 5.0]), "SPY": _bars([100] * 4)}
        panel = {"A": {DAYS[0]: [6.0, 0.0]}}
        self.assertEqual(len(pb.simulate(panel, bars, DAYS[:4], cost_bps=0.0)["trades"]), 1)
        r = pb.simulate(panel, bars, DAYS[:4], cost_bps=0.0, disable={"stop"})
        self.assertEqual((r["trades"], r["open_positions"]), ([], ["A"]))
        # A day-2 score makes the exit step run on a close (6) below both the stop (7.5)
        # and exit_decision's 8% fallback (9.2): with the stop off it must not sell there
        # either. The 1% cap still sizes against the stop: 100 / 2.5 = 40 shares = $400.
        panel["A"][DAYS[2]] = [6.0, 0.0]
        r = pb.simulate(panel, bars, DAYS[:4], cost_bps=0.0, initial=10_000.0,
                        max_trade_risk_pct=0.01, disable={"stop"})
        self.assertEqual((r["trades"], r["open_positions"]), ([], ["A"]))
        self.assertAlmostEqual(r["curve"][1][2], 400.0)

    def test_disable_stop_drops_the_fallback_floor(self):
        # No ATR, so no stop: exit_decision's 8%-of-cost fallback (9.2) is the only floor.
        bars = {"A": _bars([10, 10, 9, 9], atr=0.0), "SPY": _bars([100] * 4)}
        panel = {"A": {DAYS[0]: [6.0, 0.0], DAYS[2]: [6.0, 0.0]}}
        on = pb.simulate(panel, bars, DAYS[:4], cost_bps=0.0)
        self.assertEqual((on["trades"][0]["exit_date"], on["open_positions"]), (DAYS[3], []))
        off = pb.simulate(panel, bars, DAYS[:4], cost_bps=0.0, disable={"stop"})
        self.assertEqual((off["trades"], off["open_positions"]), ([], ["A"]))

    def test_disable_soft_exit_keeps_position(self):
        bars = {"A": _bars([10, 10, 9, 8]), "SPY": _bars([100] * 4)}
        panel = {"A": {DAYS[0]: [6.0, 0.0], DAYS[1]: [-1.0, -1.0]}}
        r = pb.simulate(panel, bars, DAYS[:4], cost_bps=0.0, disable={"soft_exit"})
        self.assertEqual((r["trades"], r["open_positions"]), ([], ["A"]))

    def test_disable_winner_protection_sells_winner(self):
        # Bought at 12 (day-1 open); day-2 close 13 above the 50-DMA (11) with S10+L60 < 0.
        bars = {"A": _bars([10, 12, 13, 13]), "SPY": _bars([100] * 4)}
        bars["A"]["sma50"] = [None, None, 11.0, 11.0]
        panel = {"A": {DAYS[0]: [6.0, 0.0], DAYS[2]: [-1.0, -1.0]}}
        self.assertEqual(pb.simulate(panel, bars, DAYS[:4], cost_bps=0.0)["open_positions"], ["A"])
        r = pb.simulate(panel, bars, DAYS[:4], cost_bps=0.0, disable={"winner_protection"})
        self.assertEqual((r["trades"][0]["exit_date"], r["open_positions"]), (DAYS[3], []))

    def test_disable_regime_uses_balanced_in_a_correction(self):
        syms = [f"S{k}" for k in range(8)]
        bars = {s: _bars([10] * 3) for s in syms} | {"SPY": _bars([100] * 3)}
        panel = {s: {DAYS[0]: [12.0, 0.0]} for s in syms}
        panel["SPY"] = {DAYS[0]: [0.0, -3.0]}                        # DEFENSIVE regime
        r = pb.simulate(panel, bars, DAYS[:3], cost_bps=0.0, initial=10_000.0, disable={"regime"})
        self.assertAlmostEqual(r["curve"][1][2], 5 * 0.15 * 10_000.0)  # BALANCED: 5 x 15%

    def test_disable_cash_buffer_and_slot_expansion(self):
        # BALANCED: day 1 fills 5 x 15% = 75%. Cash 25% > 15% opens a 6th slot, which the
        # 20% buffer limits to 80% - 75% = $500; without the buffer it gets the full 15%.
        syms = [f"S{k}" for k in range(8)]
        bars = {s: _bars([10] * 4) for s in syms} | {"SPY": _bars([100] * 4)}
        panel = {s: {DAYS[0]: [6.0, 0.0], DAYS[1]: [6.0, 0.0]} for s in syms}
        invested = {}
        for off in ((), ("cash_buffer",), ("slot_expansion",)):
            r = pb.simulate(panel, bars, DAYS[:4], cost_bps=0.0, initial=10_000.0, disable=set(off))
            invested[off] = r["curve"][2][2]
        self.assertAlmostEqual(invested[()], 8_000.0)
        self.assertAlmostEqual(invested[("cash_buffer",)], 9_000.0)
        self.assertAlmostEqual(invested[("slot_expansion",)], 7_500.0)

    def test_disable_loss_cap(self):
        bars = {"A": _bars([10] * 3), "SPY": _bars([100] * 3)}
        panel = {"A": {DAYS[0]: [6.0, 0.0]}}
        r = pb.simulate(panel, bars, DAYS[:3], cost_bps=0.0, initial=10_000.0,
                        max_trade_risk_pct=0.01, disable={"loss_cap"})
        self.assertAlmostEqual(r["curve"][1][2], 1_500.0)

    def test_rank_by_sharpe_rule_variants_only(self):
        table = {"aether_rules": {"sharpe": 0.7}, "no_regime": {"sharpe": 1.2},
                 "no_stop": {"sharpe": None}, "aether_no_cap": {"sharpe": 0.9},
                 "signal_only": {"sharpe": 5.0}, "spy_buy_hold": {"sharpe": 9.0}}
        self.assertEqual(pb.rank_by_sharpe(table), ["no_regime", "aether_no_cap", "aether_rules", "no_stop"])
        self.assertEqual(pb.pick_by_sharpe(table), "no_regime")

    def test_rank_by_sharpe_zero_is_not_missing(self):
        table = {"aether_rules": {"sharpe": 0.0}, "no_regime": {"sharpe": -0.5},
                 "no_stop": {"sharpe": None}}
        self.assertEqual(pb.rank_by_sharpe(table), ["aether_rules", "no_regime", "no_stop"])

    def test_oos_split_picks_on_train_and_ranks_on_test(self):
        cal = ["2024-12-30", "2024-12-31", "2025-01-02", "2025-01-03"]
        seen = []

        def run_table(days):
            seen.append(days)
            if days[0] < "2025-01-02":       # train: no_regime best
                return {"aether_rules": {"sharpe": 0.3}, "no_regime": {"sharpe": 1.0},
                        "no_stop": {"sharpe": 0.5}, "spy_buy_hold": {"sharpe": 9.0}}
            return {"aether_rules": {"sharpe": 1.4}, "no_regime": {"sharpe": 1.1},   # test
                    "no_stop": {"sharpe": 0.2}, "spy_buy_hold": {"sharpe": 9.0}}

        out = pb.oos_split(cal, "2025-01-02", run_table)
        # The split day itself is test, never train.
        self.assertEqual(seen, [cal[:2], cal[2:]])
        # Picked on train (a pick on test would be aether_rules), ranked 2nd of 3 on test.
        self.assertEqual((out["picked"], out["test_rank"]), ("no_regime", 2))
        self.assertEqual(out["date"], "2025-01-02")

    def test_oos_split_rejects_bad_dates(self):
        cal = ["2024-12-30", "2024-12-31", "2025-01-02", "2025-01-03"]
        with self.assertRaises(ValueError):                  # not ISO: string order would lie
            pb.oos_split(cal, "2025-1-1", lambda days: {})
        with self.assertRaises(SystemExit):                  # nothing left to test on
            pb.oos_split(cal, "2026-01-01", lambda days: {})

    def test_run_variants_names(self):
        bars = {"A": _bars([10] * 3), "SPY": _bars([100] * 3)}
        panel = {"A": {DAYS[0]: [6.0, 0.0]}}
        base = pb.run_variants(panel, bars, DAYS[:3], 0.0, frozenset(), ["A"], False)
        full = pb.run_variants(panel, bars, DAYS[:3], 0.0, frozenset(), ["A"], True)
        self.assertNotIn("no_regime", base)
        self.assertEqual(set(full) - set(base),
                         {f"no_{r}" for r in pb.RULES if r != "loss_cap"})

    def test_costs_reduce_equity(self):
        bars = {"A": _bars([10] * 4), "SPY": _bars([100] * 4)}
        panel = {"A": {DAYS[0]: [6.0, 0.0]}}
        free = pb.simulate(panel, bars, DAYS[:4], cost_bps=0.0)["curve"][-1][1]
        paid = pb.simulate(panel, bars, DAYS[:4], cost_bps=10.0)["curve"][-1][1]
        self.assertLess(paid, free)


class TestRunWiring(unittest.TestCase):
    """run() end to end on a tiny fake cache: which variants get the loss cap."""

    def _run(self, **kw):
        days = [f"2026-02-{d:02d}" for d in range(1, 26)]
        ts = {d: {"1. open": "10", "2. high": "11", "3. low": "9", "4. close": "10",
                  "5. volume": "1000"} for d in days}
        # A scores only after ATR(14) has warmed up; with no ATR there is no stop to cap against.
        panel = {"SPY": {d: [0.0, 0.0] for d in days}, "A": {d: [6.0, 0.0] for d in days[20:]}}
        with tempfile.TemporaryDirectory() as tmp:
            ohlcv_dir, sym_dir = os.path.join(tmp, "ohlcv"), os.path.join(tmp, "sym")
            os.makedirs(ohlcv_dir)
            for s in panel:
                open(os.path.join(ohlcv_dir, f"{s}_daily.json"), "w").close()
                os.makedirs(os.path.join(sym_dir, s))
            with (mock.patch.object(pb._br, "OHLCV_DIR", ohlcv_dir),
                  mock.patch.object(pb._br, "SYM_DIR", sym_dir),
                  mock.patch.object(pb._br, "load_ohlcv", return_value=ts),
                  mock.patch.object(pb, "build_panel", return_value=panel),
                  mock.patch.object(pb, "OUT_PATH", os.path.join(tmp, "out.json")),
                  mock.patch.object(pb, "_out")):
                return pb.run(min_year=2026, **kw)

    def test_cap_reaches_aether_rules_only(self):
        # Stop gap 2.5 x ATR 2 = 5 on a 10 stock: a 15% buy risks 7.5% of equity, so the
        # 1% cap must trim aether_rules' buy, and nothing else.
        m = self._run()["metrics"]
        self.assertGreaterEqual(m["aether_rules"]["cap_trims"], 1)
        self.assertEqual(m["aether_no_cap"]["cap_trims"], 0)
        self.assertEqual(m["signal_only"]["cap_trims"], 0)

    def test_cap_reaches_every_attribution_row(self):
        m = self._run(attribution=True)["metrics"]
        rows = [f"no_{r}" for r in pb.RULES if r != "loss_cap"]
        self.assertTrue(set(rows) <= set(m))
        for row in rows:
            self.assertGreaterEqual(m[row]["cap_trims"], 1, row)

    def test_payload_carries_the_stop_tail(self):
        out = self._run()
        self.assertIn("aether_rules", out["stop_tail"])
        self.assertIn("n_stop_exits", out["stop_tail"]["aether_rules"])

    def test_robust_run_end_to_end(self):
        out = self._run(split="2026-02-10", robust=3)["robustness"]
        self.assertEqual(out["n_draws"], 3)
        self.assertEqual(set(out["periods"]), {"full", "train", "test"})
        self.assertIn("no_regime", out["verdicts"])

    def test_robust_needs_split(self):
        with self.assertRaises(SystemExit):
            self._run(robust=2)

    def test_split_runs_attribution_on_both_halves(self):
        # --split implies --attribution, and train and test both carry every no_* row.
        out = self._run(split="2026-02-10")
        rows = {f"no_{r}" for r in pb.RULES if r != "loss_cap"}
        self.assertTrue(rows <= set(out["metrics"]))
        self.assertTrue(rows <= set(out["split"]["train"]))
        self.assertTrue(rows <= set(out["split"]["test"]))
        self.assertEqual(out["split"]["train"]["spy_buy_hold"]["end"], "2026-02-09")
        self.assertEqual(out["split"]["test"]["spy_buy_hold"]["start"], "2026-02-10")


class TestHurst(unittest.TestCase):
    """R&D #54b: the rolling Hurst estimator, the entry filter and the pre-registered verdict."""

    @staticmethod
    def _median_h(x):
        vals = [h for h in pb.rolling_hurst(x) if h is not None]
        return sorted(vals)[len(vals) // 2]

    def test_random_walk_is_about_one_half(self):
        rng = np.random.default_rng(7)
        self.assertAlmostEqual(self._median_h(rng.standard_normal(3000)), 0.5, delta=0.08)

    def test_random_walk_null_distribution_on_one_window(self):
        # The gate depends on this noise (#210 review): on a single 120-return window, a pure
        # random walk reads H ~ 0.46 +/- 0.08, so a "skip H < 0.50" filter drops most
        # memoryless stocks. Any future threshold should sit below the null's P5 (~0.33).
        rng = np.random.default_rng(0)
        h = np.array([pb.rolling_hurst(rng.standard_normal(pb.HURST_WINDOW))[-1] for _ in range(1000)])
        self.assertAlmostEqual(float(h.mean()), 0.46, delta=0.02)
        self.assertAlmostEqual(float(h.std()), 0.078, delta=0.015)
        self.assertGreater(float((h < 0.50).mean()), 0.6)
        self.assertAlmostEqual(float(np.percentile(h, 5)), 0.33, delta=0.03)

    def test_mean_reverting_is_below_and_persistent_above(self):
        rng = np.random.default_rng(7)
        e = rng.standard_normal(3000)
        reverting = e[1:] - 0.9 * e[:-1]                    # MA(1), negative autocorrelation
        persistent = np.zeros(3000)
        for t in range(1, 3000):
            persistent[t] = 0.5 * persistent[t - 1] + e[t]  # AR(1), positive autocorrelation
        self.assertLess(self._median_h(reverting), 0.35)
        self.assertGreater(self._median_h(persistent), 0.6)

    def test_no_value_before_the_window_and_no_look_ahead(self):
        rng = np.random.default_rng(3)
        x = rng.standard_normal(300)
        h = pb.rolling_hurst(x)
        self.assertIsNone(h[pb.HURST_WINDOW - 2])
        self.assertIsNotNone(h[pb.HURST_WINDOW - 1])
        y = x.copy()
        y[200:] = 50.0 * rng.standard_normal(100)          # change only the future
        self.assertEqual(pb.rolling_hurst(y)[199], h[199])
        self.assertNotEqual(pb.rolling_hurst(y)[200], h[200])

    def test_filter_skips_low_h_and_allows_unknown(self):
        bars = {s: _bars([10] * 3) for s in ("A", "B", "C")} | {"SPY": _bars([100] * 3)}
        bars["A"]["hurst"] = [0.40] * 3                     # below 0.5: skipped
        bars["B"]["hurst"] = [0.60] * 3                     # above: bought
        bars["C"]["hurst"] = [None] * 3                     # not enough history: allowed
        panel = {s: {DAYS[0]: [6.0, 0.0]} for s in ("A", "B", "C")}
        r = pb.simulate(panel, bars, DAYS[:3], cost_bps=0.0, hurst_filter=("hurst", 0.5))
        self.assertEqual(r["open_positions"], ["B", "C"])
        self.assertEqual(pb.simulate(panel, bars, DAYS[:3], cost_bps=0.0)["open_positions"], ["A", "B", "C"])

    def test_bars_carry_both_hurst_series(self):
        days = [f"2025-{m:02d}-{d:02d}" for m in range(1, 13) for d in range(1, 28)][:200]
        rng = np.random.default_rng(1)
        px = 100 * np.exp(np.cumsum(0.01 * rng.standard_normal(200)))
        ts = {d: {"1. open": p, "2. high": p * 1.01, "3. low": p * 0.99, "4. close": p, "5. volume": 1}
              for d, p in zip(days, px, strict=True)}
        b = pb.bars_from_series(ts)
        self.assertEqual(len(b["hurst"]), 200)
        self.assertIsNone(b["hurst"][pb.HURST_WINDOW - 1])  # needs WINDOW returns = WINDOW + 1 bars
        self.assertIsNotNone(b["hurst"][pb.HURST_WINDOW])
        self.assertIsNotNone(b["hurst_abs"][-1])

    def test_run_variants_adds_the_five_hurst_variants(self):
        bars = {"A": _bars([10] * 3), "SPY": _bars([100] * 3)}
        panel = {"A": {DAYS[0]: [6.0, 0.0]}}
        out = pb.run_variants(panel, bars, DAYS[:3], 0.0, frozenset(), ["A"], False, hurst=True)
        self.assertTrue(set(pb.HURST_VARIANTS) <= set(out))
        base = pb.run_variants(panel, bars, DAYS[:3], 0.0, frozenset(), ["A"], False)
        self.assertFalse(set(pb.HURST_VARIANTS) & set(base))

    def _tables(self, train_sharpes, test_picked, base=(1.0, -10.0)):
        train = {k: {"sharpe": s} for k, s in zip(pb.HURST_VARIANTS, train_sharpes, strict=True)}
        test = {k: {"sharpe": 0.0, "max_drawdown_pct": -50.0} for k in pb.HURST_VARIANTS}
        best = max(train, key=lambda k: train[k]["sharpe"])
        test[best] = {"sharpe": test_picked[0], "max_drawdown_pct": test_picked[1]}
        test["aether_rules"] = {"sharpe": base[0], "max_drawdown_pct": base[1]}
        return train, test

    def test_verdict_picks_on_train_and_needs_both_conditions(self):
        train, test = self._tables([0.1, 0.9, 0.2, 0.3, 0.4], (1.2, -8.0))
        v = pb.hurst_verdict(train, test)
        self.assertEqual(v["picked"], "aether_hurst_r50")
        self.assertTrue(v["passed"])
        self.assertFalse(pb.hurst_verdict(*self._tables([0.1, 0.9, 0.2, 0.3, 0.4], (0.9, -8.0)))["passed"])
        self.assertFalse(pb.hurst_verdict(*self._tables([0.1, 0.9, 0.2, 0.3, 0.4], (1.2, -12.0)))["passed"])

    def test_verdict_ignores_test_when_picking(self):
        # The best TEST Sharpe belongs to a variant that was poor on train: it must not be picked.
        train, test = self._tables([0.9, 0.1, 0.2, 0.3, 0.4], (0.5, -8.0))
        test["aether_hurst_r50"] = {"sharpe": 3.0, "max_drawdown_pct": -1.0}
        self.assertEqual(pb.hurst_verdict(train, test)["picked"], "aether_hurst_r45")


class TestRobustness(unittest.TestCase):
    """R&D #62: perturbed paths, the summary and the pre-registered verdicts."""

    SYMS = ["SPY"] + [f"S{k:02d}" for k in range(20)]
    CAL = [f"2024-01-{d:02d}" for d in range(1, 29)] + [f"2025-02-{d:02d}" for d in range(1, 29)]

    def test_paths_are_reproducible_and_keep_spy(self):
        a = pb.draw_paths(self.SYMS, self.CAL, 10, seed=62)
        self.assertEqual(a, pb.draw_paths(self.SYMS, self.CAL, 10, seed=62))
        self.assertNotEqual(a, pb.draw_paths(self.SYMS, self.CAL, 10, seed=63))
        for path in a:
            self.assertIn("SPY", path["symbols"])
            self.assertEqual(len(path["symbols"]), 1 + 16)          # 80% of 20 others, plus SPY
            self.assertLessEqual(self.CAL.index(path["start"]), pb.ROBUST_MAX_OFFSET)
        self.assertGreater(len({p["symbols"] for p in a}), 1)

    def test_path_universe_cuts_panel_and_eligible(self):
        panel = {"SPY": {}, "A": {}, "B": {}, "C": {}}
        sub, elig = pb.path_universe(panel, ["A", "B", "C"], frozenset({"SPY", "B", "Z"}))
        self.assertEqual(set(sub), {"SPY", "B"})
        self.assertEqual(elig, ["B"])

    def test_offset_cap_keeps_two_days_before_the_split(self):
        cal = [f"2025-01-{d:02d}" for d in range(1, 11)] + [f"2025-03-{d:02d}" for d in range(1, 11)]
        for path in pb.draw_paths(self.SYMS, cal, 30, split="2025-03-01"):
            self.assertGreaterEqual(sum(path["start"] <= d < "2025-03-01" for d in cal), 2)
        # A long first half leaves the pre-registered 0..60 range untouched.
        long_cal = [f"d{i:04d}" for i in range(600)]
        offs = {long_cal.index(p["start"]) for p in pb.draw_paths(self.SYMS, long_cal, 200, split="d0500")}
        self.assertEqual((min(offs), max(offs)), (0, pb.ROBUST_MAX_OFFSET))

    @staticmethod
    def _draw(base, other, spy):
        tbl = lambda b, o, s: {"aether_rules": {"sharpe": b, "cagr_pct": 1.0, "max_drawdown_pct": -5.0},
                               "no_regime": {"sharpe": o, "cagr_pct": 2.0, "max_drawdown_pct": -6.0},
                               "spy_buy_hold": {"sharpe": s, "cagr_pct": 3.0, "max_drawdown_pct": -7.0}}
        return {"full": tbl(base, other, spy), "train": tbl(base, other, spy), "test": tbl(base, other, spy)}

    def test_summary_win_rate_and_verdicts(self):
        # no_regime beats aether_rules in 9 of 10 draws; SPY in 1 of 10.
        draws = [self._draw(1.0, 1.5 if i < 9 else 0.5, 0.5 if i < 9 else 2.0) for i in range(10)]
        s = pb.robustness_summary(draws)
        self.assertEqual(s["periods"]["test"]["no_regime"]["win_rate"], 0.9)
        self.assertEqual(s["periods"]["test"]["spy_buy_hold"]["win_rate"], 0.1)
        self.assertEqual(s["periods"]["test"]["no_regime"]["sharpe"]["median"], 1.5)
        self.assertEqual(s["verdicts"], {"no_regime": "robustly better", "spy_buy_hold": "robustly worse"})

    def test_summary_against_another_base(self):
        # R&D #64 judges against SPY: the win rate is then measured against spy_buy_hold.
        draws = [self._draw(1.0, 1.5, 2.0 if i < 9 else 0.5) for i in range(10)]
        s = pb.robustness_summary(draws, base="spy_buy_hold")
        self.assertEqual(s["periods"]["test"]["aether_rules"]["win_rate"], 0.1)
        self.assertEqual(s["periods"]["test"]["no_regime"]["win_rate"], 0.1)
        self.assertNotIn("spy_buy_hold", s["verdicts"])

    def test_verdict_needs_both_halves(self):
        draws = [self._draw(1.0, 1.5, 0.5) for _ in range(10)]
        for d in draws:
            d["train"]["no_regime"]["sharpe"] = 0.5                 # loses on train, wins on test
        self.assertEqual(pb.robustness_summary(draws)["verdicts"]["no_regime"], "inconclusive")

    def test_win_rate_boundaries(self):
        at80 = [self._draw(1.0, 1.5 if i < 8 else 0.5, 1.0) for i in range(10)]
        self.assertEqual(pb.robustness_summary(at80)["verdicts"]["no_regime"], "robustly better")
        at70 = [self._draw(1.0, 1.5 if i < 7 else 0.5, 1.0) for i in range(10)]
        self.assertEqual(pb.robustness_summary(at70)["verdicts"]["no_regime"], "inconclusive")
        ties = [self._draw(1.0, 1.0, 1.0) for _ in range(10)]   # a tie is not a win
        self.assertEqual(pb.robustness_summary(ties)["periods"]["test"]["no_regime"]["win_rate"], 0.0)

    def test_run_robustness_periods_use_the_split_and_the_start(self):
        seen = []

        def run_table(syms, days):
            seen.append((syms, days[0], days[-1]))
            return {"aether_rules": {"sharpe": 1.0}, "no_regime": {"sharpe": 2.0}}

        paths = [{"symbols": frozenset({"SPY", "A"}), "start": "2024-01-05"}]
        out = pb.run_robustness(paths, self.CAL, "2025-02-01", run_table)
        self.assertEqual([s[1:] for s in seen], [("2024-01-05", "2025-02-28"), ("2024-01-05", "2024-01-28"),
                                                ("2025-02-01", "2025-02-28")])
        self.assertEqual(out["n_draws"], 1)
        self.assertEqual(out["per_draw_sharpe"][0]["test"]["no_regime"], 2.0)


class TestStopTail(unittest.TestCase):
    @staticmethod
    def _t(pnl, exit_=9.0, stop=9.0, reason="stop"):
        return {"reason": reason, "exit": exit_, "stop": stop, "pnl_pct_equity": pnl}

    def test_no_stop_exits(self):
        self.assertEqual(pb.stop_tail([self._t(-5.0, reason="momentum decay")], 0.01),
                         {"n_stop_exits": 0})

    def test_percentiles_and_gate(self):
        trades = [self._t(-1.0) for _ in range(19)] + [self._t(-3.5, exit_=8.0)]
        trades.append(self._t(-9.0, reason="momentum decay"))          # not a stop: ignored
        tail = pb.stop_tail(trades, 0.01)
        self.assertEqual((tail["n_stop_exits"], tail["n_gap_through"]), (20, 1))
        self.assertEqual(tail["loss_median_pct"], 1.0)
        self.assertEqual(tail["loss_worst_pct"], 3.5)
        self.assertEqual(tail["worst_trade"]["pnl_pct_equity"], -3.5)
        self.assertTrue(tail["gate_fail"])                               # worst 3.5% > 3%
        ok = pb.stop_tail([self._t(-1.0) for _ in range(20)], 0.01)
        self.assertFalse(ok["gate_fail"])
        self.assertEqual(ok["share_over_cap"], 0.0)

    def test_no_cap_means_no_gate(self):
        tail = pb.stop_tail([self._t(-2.0)], None)
        self.assertNotIn("gate_fail", tail)


class TestMetricsAndBenchmarks(unittest.TestCase):
    def test_metrics_cagr_and_drawdown(self):
        curve = [("2025-01-01", 100.0, 0.0), ("2025-07-02", 50.0, 0.0), ("2026-01-01", 121.0, 0.0)]
        m = pb.metrics({"curve": curve, "trades": [], "traded_usd": 0.0})
        self.assertAlmostEqual(m["max_drawdown_pct"], -50.0)
        self.assertAlmostEqual(m["total_return_pct"], 21.0)
        self.assertAlmostEqual(m["cagr_pct"], 21.0, delta=0.1)

    def test_buy_and_hold_equal_weight(self):
        bars = {"A": _bars([10, 20]), "B": _bars([10, 10])}
        r = pb.buy_and_hold(bars, DAYS[:2], ["A", "B"], cost_bps=0.0, initial=1000.0)
        self.assertAlmostEqual(r["curve"][-1][1], 1500.0)

    def test_synthetic_bars_dropped(self):
        real = {"1. open": "10", "2. high": "11", "3. low": "9", "4. close": "10", "5. volume": "500"}
        days = [f"2026-02-{d:02d}" for d in range(1, 29)]
        ts = {d: dict(real) for d in days}
        ts[days[5]] = {"1. open": "10", "2. high": "10", "3. low": "10", "4. close": "10",
                       "5. volume": "0"}
        ts[days[6]] = dict(real, provisional=True)
        b = pb.bars_from_series(ts)
        self.assertNotIn(days[5], b["idx"])
        self.assertNotIn(days[6], b["idx"])
        self.assertEqual(len(b["idx"]), len(days) - 2)

    def test_bars_from_series_without_split(self):
        ts = {d: {"1. open": "10", "2. high": "11", "3. low": "9", "4. close": "10"} for d in DAYS}
        b = pb.bars_from_series(ts)
        self.assertEqual(b["o"][0], 10.0)
        self.assertIsNone(b["sma50"][-1])                            # < 50 bars
        self.assertGreater(b["atr"][-1], 0.0)


if __name__ == "__main__":
    unittest.main()
