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

    def test_split_runs_attribution_on_both_halves(self):
        # --split implies --attribution, and train and test both carry every no_* row.
        out = self._run(split="2026-02-10")
        rows = {f"no_{r}" for r in pb.RULES if r != "loss_cap"}
        self.assertTrue(rows <= set(out["metrics"]))
        self.assertTrue(rows <= set(out["split"]["train"]))
        self.assertTrue(rows <= set(out["split"]["test"]))
        self.assertEqual(out["split"]["train"]["spy_buy_hold"]["end"], "2026-02-09")
        self.assertEqual(out["split"]["test"]["spy_buy_hold"]["start"], "2026-02-10")


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
