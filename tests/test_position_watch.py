"""Tests for scripts/monitoring/position_watch.py: the daily, recommend-only position monitor.

Pins the pieces the daily call depends on, on synthetic data: the peer beta / residual split,
replacement timing, the concentration floor, each branch of the decision, the tax-lot order
with the wash-sale window, and the RETRO scoring and journal.
"""
import datetime
import math
import os
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "monitoring"))
import position_watch as pw  # noqa: E402

CFG = dict(pw.DEFAULTS)


def _bars(closes, opens=None, vols=None):
    n = len(closes)
    days = [(datetime.date(2025, 1, 1) + datetime.timedelta(days=k)).isoformat() for k in range(n)]
    return {"days": days, "idx": {d: i for i, d in enumerate(days)}, "o": list(opens or closes),
            "h": list(closes), "l": list(closes), "c": list(closes), "v": list(vols or [100.0] * n)}


def _metrics(vs_sma50=-0.05, ret_1=-0.01):
    return {"vs_sma50": vs_sma50, "ret_1": ret_1}


def _resid(z):
    return {"z_20": z, "resid_20": z * 0.02, "beta": 1.0}


def _repl(ok=True, stretch=0.01):
    return {"SPY": {"entry_ok": ok, "stretch_50": stretch, "close": 100.0}}


class TestPeers(unittest.TestCase):
    def test_beta_and_residual(self):
        rng = np.random.default_rng(1)
        peer = list(rng.normal(0, 0.01, 200))
        own = list(rng.normal(0, 0.005, 200))
        sym = [1.5 * p + o for p, o in zip(peer, own, strict=True)]
        sym[-20:] = [x - 0.004 for x in sym[-20:]]             # a 20-day run of weakness on its own
        r = pw.beta_residual(sym, peer)
        self.assertAlmostEqual(r["beta"], 1.5, delta=0.15)
        self.assertLess(r["resid_20"], -0.05)
        self.assertLess(r["z_20"], -1.0)

    def test_sector_move_is_not_own_weakness(self):
        rng = np.random.default_rng(2)
        peer = list(rng.normal(0, 0.01, 200))
        peer[-20:] = [x - 0.01 for x in peer[-20:]]            # the whole sector falls
        sym = [p + o for p, o in zip(peer, rng.normal(0, 0.003, 200), strict=True)]
        r = pw.beta_residual(sym, peer)
        self.assertGreater(r["z_20"], -1.0)                    # the symbol only followed the sector

    def test_too_little_history(self):
        self.assertIsNone(pw.beta_residual([0.01] * 30, [0.01] * 30))

    def test_basket_returns_equal_weight(self):
        a, b = _bars([10, 11, 11]), _bars([20, 20, 22])
        r = pw.basket_returns([a, b], a["days"])
        self.assertAlmostEqual(r[0], math.log(1.1) / 2)
        self.assertAlmostEqual(r[1], math.log(1.1) / 2)


class TestTimingAndConcentration(unittest.TestCase):
    def test_replacement_stretched_or_ok(self):
        flat = _bars([100.0] * 60)
        self.assertTrue(pw.replacement_timing(flat, 59)["entry_ok"])
        spike = _bars([100.0] * 59 + [112.0])                  # 11% above a flat 50-day average
        self.assertFalse(pw.replacement_timing(spike, 59)["entry_ok"])

    def test_rsi_extremes(self):
        self.assertEqual(pw.rsi(list(range(1, 30)), 28), 100.0)
        self.assertLess(pw.rsi(list(range(30, 1, -1)), 28), 1.0)
        self.assertEqual(pw.rsi([5.0] * 30, 29), 50.0)

    def test_concentration_headroom(self):
        c = pw.concentration(400_000, 1_000_000, 0.30)
        self.assertEqual((c["pct"], c["headroom_usd"]), (0.4, 100_000))
        self.assertEqual(pw.concentration(250_000, 1_000_000, 0.30)["headroom_usd"], 0.0)

    def test_symbol_metrics(self):
        b = _bars([100.0] * 250 + [90.0], opens=[100.0] * 250 + [92.0])
        m = pw.symbol_metrics(b, 250)
        self.assertAlmostEqual(m["ret_1"], -0.10)
        self.assertAlmostEqual(m["gap"], -0.08)
        self.assertAlmostEqual(m["drawdown_52w"], -0.10)
        self.assertLess(m["vs_sma50"], 0)


class TestDecide(unittest.TestCase):
    conc = pw.concentration(500_000, 1_000_000, 0.30)

    def test_trim_when_weak_alone_and_entry_ok(self):
        d = pw.decide(_metrics(), _resid(-1.5), [], _repl(), self.conc, CFG)
        self.assertEqual((d["action"], d["replacement"]), ("TRIM", "SPY"))
        self.assertAlmostEqual(d["tranche_usd"], 0.05 * 500_000)

    def test_tranche_never_crosses_the_floor(self):
        near = pw.concentration(330_000, 1_000_000, 0.30)     # 33%: 3 pts above, $30K headroom
        d = pw.decide(_metrics(), _resid(-1.5), [], _repl(), near, dict(CFG, tranche_pct=0.5))
        self.assertAlmostEqual(d["tranche_usd"], 30_000)

    def test_hold_at_floor(self):
        at = pw.concentration(315_000, 1_000_000, 0.30)       # inside the 2-point buffer
        self.assertEqual(pw.decide(_metrics(), _resid(-2.0), [], _repl(), at, CFG)["action"], "HOLD")

    def test_hold_when_recovery_forming(self):
        d = pw.decide(_metrics(), _resid(-2.0), ["RBR↑(gap,watch)"], _repl(), self.conc, CFG)
        self.assertEqual(d["action"], "HOLD")

    def test_hold_when_only_the_sector_is_weak(self):
        self.assertEqual(pw.decide(_metrics(), _resid(-0.5), [], _repl(), self.conc, CFG)["action"], "HOLD")

    def test_hold_above_the_50_day(self):
        self.assertEqual(pw.decide(_metrics(vs_sma50=0.02), _resid(-2.0), [], _repl(), self.conc, CFG)["action"],
                         "HOLD")

    def test_wait_on_a_crash_day(self):
        d = pw.decide(_metrics(ret_1=-0.08), _resid(-2.0), [], _repl(), self.conc, CFG)
        self.assertEqual(d["action"], "WAIT")

    def test_wait_when_replacement_stretched(self):
        d = pw.decide(_metrics(), _resid(-2.0), [], _repl(ok=False, stretch=0.08), self.conc, CFG)
        self.assertEqual(d["action"], "WAIT")


class TestDoubts(unittest.TestCase):
    TUE = datetime.date(2026, 11, 3)

    def _doubts(self, bar="2026-11-02", peers=None, repl=None, health="ok", live=True, live_ok=True, today=TUE):
        return pw.data_doubts(bar, peers or {"AMD": "2026-11-02"}, repl or {"SPY": "2026-11-02"}, health,
                              live, live_ok, today)

    def test_fresh_data_has_no_doubts(self):
        self.assertEqual(self._doubts(), [])
        self.assertEqual(self._doubts(bar="2026-11-03", peers={"AMD": "2026-11-03"},
                                      repl={"SPY": "2026-11-03"}), [])            # after the close
        self.assertEqual(self._doubts(bar="2026-10-30", today=datetime.date(2026, 11, 2)), [])  # Monday

    def test_stale_history_is_a_doubt(self):
        self.assertIn("2026-10-30", self._doubts(bar="2026-10-30")[0])
        self.assertEqual(self._doubts(bar="2026-11-25", peers={"AMD": "2026-11-25"}, repl={"SPY": "2026-11-25"},
                                      today=datetime.date(2026, 11, 27)), [])      # after Thanksgiving

    def test_behind_or_missing_inputs(self):
        d = self._doubts(peers={"AMD": "2026-10-30", "ON": None}, repl={"SPY": None})
        self.assertEqual(len(d), 2)
        self.assertIn("AMD, ON", d[0])
        self.assertIn("SPY", d[1])

    def test_failed_pack_and_failed_quote(self):
        self.assertEqual(len(self._doubts(health="failed", live_ok=False)), 2)
        self.assertEqual(self._doubts(live=False, live_ok=False), [])              # no quote asked for

    def test_trim_with_a_doubt_becomes_wait(self):
        trim = {"action": "TRIM", "reasons": ["weak"], "tranche_usd": 5000.0, "replacement": "SPY"}
        w = pw.apply_doubts(trim, ["account snapshot failed"])
        self.assertEqual((w["action"], w["tranche_usd"], w["replacement"]), ("WAIT", 0.0, None))
        self.assertIn("not sure: account snapshot failed", w["reasons"])
        self.assertIs(pw.apply_doubts(trim, []), trim)
        hold = {"action": "HOLD", "reasons": [], "tranche_usd": 0.0, "replacement": None}
        self.assertIs(pw.apply_doubts(hold, ["x"]), hold)


class TestLots(unittest.TestCase):
    lots = [{"acquired": "2018-01-02", "qty": 100, "price": 20.0},      # LT, big gain
            {"acquired": "2024-01-02", "qty": 100, "price": 50.0},      # LT, small gain
            {"acquired": "2026-06-01", "qty": 50, "price": 110.0},      # ST, loss
            {"acquired": "2026-03-02", "qty": 80, "price": 45.0}]       # ST, gain

    def test_losses_first_then_smallest_lt_gain(self):
        cfg = dict(CFG, acquisition_dates=["2026-11-30"])
        r = pw.pick_lots(self.lots, 9_000, 60.0, datetime.date(2027, 1, 5), cfg)
        self.assertEqual([x["acquired"] for x in r["lots"]], ["2026-06-01", "2024-01-02"])
        self.assertFalse(r["wash_window"])

    def test_no_loss_sales_inside_a_wash_window(self):
        cfg = dict(CFG, acquisition_dates=["2026-11-30"])
        r = pw.pick_lots(self.lots, 3_000, 60.0, datetime.date(2026, 11, 10), cfg)
        self.assertTrue(r["wash_window"])
        self.assertNotIn("2026-06-01", [x["acquired"] for x in r["lots"]])
        self.assertEqual(r["lots"][0]["acquired"], "2024-01-02")

    def test_feb_29_lot_does_not_crash(self):
        self.assertEqual(pw.anniversary(datetime.date(2024, 2, 29)), datetime.date(2025, 3, 1))
        self.assertEqual(pw.anniversary(datetime.date(2024, 3, 1)), datetime.date(2025, 3, 1))
        lots = [{"acquired": "2024-02-29", "qty": 10, "price": 50.0}]
        for day, lt in ((datetime.date(2025, 3, 1), False), (datetime.date(2025, 3, 2), True)):
            r = pw.pick_lots(lots, 300, 60.0, day, dict(CFG, acquisition_dates=[]))
            self.assertEqual(r["lots"][0]["term"], "LT" if lt else "ST")

    def test_tax_estimate(self):
        r = pw.pick_lots(self.lots, 600, 60.0, datetime.date(2027, 1, 5), dict(CFG, acquisition_dates=[]))
        # Only the loss lot is needed: 10 sh x (60 - 110) = -500 short-term.
        self.assertEqual((r["shares"], r["lots"][0]["gain"]), (10, -500.0))
        self.assertAlmostEqual(r["est_tax"], -500 * CFG["st_rate"])


class TestRetroAndJournal(unittest.TestCase):
    def test_retro_scores_trim_and_hold(self):
        j = [{"date": "d1", "price": 60.0, "action": "TRIM", "replacement": "SPY", "replacements": {"SPY": 700.0}},
             {"date": "d2", "price": 60.0, "action": "HOLD", "replacement": None, "replacements": {"SPY": 700.0}}]
        r = pw.retro(j, 57.0, {"SPY": 707.0})                  # INTC -5%, SPY +1%
        self.assertEqual([s["good"] for s in r["scored"]], [True, False])
        self.assertEqual(r["hit_rate"], 0.5)

    def test_retro_needs_a_newer_bar(self):
        j = [{"date": "2026-11-03", "bar": "2026-11-02", "price": 60.0, "action": "WAIT", "replacement": None,
              "replacements": {"SPY": 700.0}}]
        self.assertEqual(pw.retro(j, 60.0, {"SPY": 700.0}, "2026-11-04", "2026-11-02")["scored"], [])   # stale
        self.assertEqual(len(pw.retro(j, 58.0, {"SPY": 710.0}, "2026-11-04", "2026-11-04")["scored"]), 1)

    def test_retro_skips_todays_call(self):
        j = [{"date": "2026-11-02", "price": 60.0, "action": "HOLD", "replacement": None,
              "replacements": {"SPY": 700.0}},
             {"date": "2026-11-03", "price": 57.0, "action": "WAIT", "replacement": None,
              "replacements": {"SPY": 707.0}}]
        r = pw.retro(j, 57.0, {"SPY": 707.0}, "2026-11-03")
        self.assertEqual([s["date"] for s in r["scored"]], ["2026-11-02"])

    def test_same_day_run_replaces_entry_and_keeps_lessons(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "INTC_journal.jsonl")
            pw.append_journal(p, {"date": "d1", "action": "HOLD"})
            pw.append_journal(p, {"date": "d2", "action": "WAIT"})
            pw.add_lesson(p, "stale bars")
            pw.append_journal(p, {"date": "d2", "action": "TRIM"})
            rows = pw.read_journal(p)
            self.assertEqual([(x["date"], x["action"]) for x in rows], [("d1", "HOLD"), ("d2", "TRIM")])
            self.assertEqual(rows[-1]["lessons"], ["stale bars"])

    def test_journal_append_and_lesson(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "x", "INTC_journal.jsonl")
            self.assertFalse(pw.add_lesson(p, "nothing yet"))
            pw.append_journal(p, {"date": "d1", "action": "HOLD"})
            pw.append_journal(p, {"date": "d2", "action": "TRIM"})
            self.assertTrue(pw.add_lesson(p, "sector move, not INTC"))
            rows = pw.read_journal(p)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[-1]["lessons"], ["sector move, not INTC"])
            self.assertNotIn("lessons", rows[0])


class TestLivePriceFallback(unittest.TestCase):
    def test_failed_quote_returns_none(self):
        with mock.patch.object(pw, "ETradeClient", side_effect=RuntimeError("Missing config")):
            with self.assertLogs(pw._log.name, level="WARNING"):
                self.assertIsNone(pw._live_price("INTC"))


class TestPackHoldings(unittest.TestCase):
    def test_symbol_value_is_taken_out_of_the_snapshot_net(self):
        pack = {"meta": {"health": "degraded"},
                "state": {"accounts": [{"net_value": 1000.0}, {"net_value": 500.0}],
                          "positions": [{"symbol": "INTC", "qty": 10, "mval": 300.0},
                                        {"symbol": "SPY", "qty": 1, "mval": 200.0}]}}
        with mock.patch.object(pw, "build_oceanview_context", return_value=pack):
            shares, other, meta = pw._pack_holdings("INTC")
        self.assertEqual((shares, other, meta["health"]), (10, 1200.0, "degraded"))


def _session_bars(n=300, drift=0.0, seed=0, end=None):
    """n session bars ending on the last completed session (so no stale-history doubt)."""
    end = end or pw.market_calendar.last_session_on_or_before(datetime.date.today() - datetime.timedelta(days=1))
    days, d = [], end
    while len(days) < n:
        if pw.market_calendar.is_session(d):
            days.append(d.isoformat())
        d -= datetime.timedelta(days=1)
    days.reverse()
    rng = np.random.default_rng(seed)
    closes = list(100 * np.exp(np.cumsum(rng.normal(drift, 0.01, n))))
    return {"days": days, "idx": {x: i for i, x in enumerate(days)}, "o": closes, "h": closes, "l": closes,
            "c": closes, "v": [1e6] * n}


class TestBoundaries(unittest.TestCase):
    def test_rsi_edge_at_70(self):
        b = _bars([100.0] * 60)
        with mock.patch.object(pw, "rsi", return_value=70.0):
            self.assertFalse(pw.replacement_timing(b, 59)["entry_ok"])
        with mock.patch.object(pw, "rsi", return_value=69.9):
            self.assertTrue(pw.replacement_timing(b, 59)["entry_ok"])

    def test_overbought_replacement_is_not_an_entry(self):
        b = _bars([100.0] * 50 + [100.0 + 0.1 * k for k in range(1, 16)])   # +1.5%, but RSI 100
        r = pw.replacement_timing(b, 64)
        self.assertLess(r["stretch_50"], pw.STRETCH_MAX)
        self.assertFalse(r["entry_ok"])

    def test_wash_window_edges(self):
        self.assertTrue(pw.in_wash_window(datetime.date(2026, 12, 30), ["2026-11-30"]))    # day 30
        self.assertFalse(pw.in_wash_window(datetime.date(2026, 12, 31), ["2026-11-30"]))   # day 31
        self.assertTrue(pw.in_wash_window(datetime.date(2026, 10, 31), ["2026-11-30"]))    # 30 before

    def test_crash_day_edge(self):
        conc = pw.concentration(500_000, 1_000_000, 0.30)
        at = pw.decide(_metrics(ret_1=-0.05), _resid(-1.5), [], _repl(), conc, CFG)
        below = pw.decide(_metrics(ret_1=-0.0501), _resid(-1.5), [], _repl(), conc, CFG)
        self.assertEqual((at["action"], below["action"]), ("TRIM", "WAIT"))

    def test_placeholder_bars_are_dropped(self):
        ts = {(datetime.date(2025, 1, 1) + datetime.timedelta(days=k)).isoformat():
              {"1. open": "10", "2. high": "10", "3. low": "10", "4. close": "10", "5. volume": "100"}
              for k in range(40)}
        ts["2025-01-05"]["provisional"] = True
        ts["2025-01-06"]["5. volume"] = "0"
        days = pw.bars_from_series(ts)["days"]
        self.assertEqual(len(days), 38)
        self.assertNotIn("2025-01-05", days)
        self.assertNotIn("2025-01-06", days)


class TestRun(unittest.TestCase):
    """run() wiring: bars, the account snapshot and the quote are patched; no files, no network."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        bars = {s: _session_bars(seed=i) for i, s in enumerate(["INTC", *CFG["peers"], "SPY", "RSP"])}
        self.cfg = os.path.join(self._tmp.name, "config.json")
        with open(self.cfg, "w", encoding="utf-8") as f:
            f.write('{"stock_plan_shares": 100, "outside_assets_usd": 1000}')
        self._p = [mock.patch.object(pw, "load_bars", side_effect=lambda s: bars.get(s)),
                   mock.patch.object(pw, "watch_dir", return_value=self._tmp.name)]
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in self._p:
            p.stop()
        self._tmp.cleanup()

    def _run(self, pack=(10, 500.0, {"health": "ok"}), live_px=None, live=False, call=None):
        with mock.patch.object(pw, "_pack_holdings", return_value=pack),                 mock.patch.object(pw, "_live_price", return_value=live_px),                 mock.patch.object(pw, "decide", side_effect=(lambda *a: dict(call)) if call else pw.decide):
            return pw.run("INTC", live=live, journal=False, config_path=self.cfg)

    def test_no_account_snapshot_gives_hold_not_a_crash(self):
        r = self._run(pack=(None, None, {"health": "failed"}))
        self.assertIsNone(r["concentration"]["pct"])
        self.assertEqual(r["call"]["action"], "HOLD")
        self.assertIn("account snapshot failed: the share of the total is uncertain", r["doubts"])
        with mock.patch.object(pw, "_out"):
            pw.print_report(r)                                   # prints "-" for the unknowns

    def test_total_counts_stock_plan_shares(self):
        r = self._run(live_px=50.0, live=True)
        c = r["concentration"]
        self.assertEqual(c["value"], (10 + 100) * 50.0)            # brokerage + stock plan
        self.assertEqual(c["total"], 500.0 + (10 + 100) * 50.0 + 1000)
        self.assertEqual((r["price"], r["price_source"]), (50.0, "live quote"))

    def test_doubts_turn_a_trim_into_wait(self):
        trim = {"action": "TRIM", "reasons": ["weak"], "tranche_usd": 100.0, "replacement": "SPY"}
        self.assertEqual(self._run(call=trim)["call"]["action"], "TRIM")                  # nothing in doubt
        r = self._run(call=trim, live=True, live_px=None)                                 # quote failed
        self.assertEqual(r["call"]["action"], "WAIT")
        self.assertIn("not sure: live quote unavailable: using the last close", r["call"]["reasons"])
        r = self._run(call=trim, pack=(10, 500.0, {"health": "failed"}))
        self.assertEqual(r["call"]["action"], "WAIT")

    def test_retro_skips_todays_call(self):
        with mock.patch.object(pw, "retro", wraps=pw.retro) as retro:
            self._run()
        args = retro.call_args.args
        self.assertEqual(len(args), 5, "retro() must be told today's date and the current bar")
        self.assertEqual(args[3], datetime.date.today().isoformat())
        self.assertEqual(args[4], pw.load_bars("INTC")["days"][-1])


class TestPackHoldingsMissing(unittest.TestCase):
    def test_no_positions_or_accounts_means_unknown(self):
        for state in ({"positions": None, "accounts": None}, {"positions": [], "accounts": None}, {}):
            with mock.patch.object(pw, "build_oceanview_context",
                                   return_value={"meta": {"health": "failed"}, "state": state}):
                self.assertEqual(pw._pack_holdings("INTC")[:2], (None, None))


class TestConfig(unittest.TestCase):
    def test_private_config_overrides_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "config.json")
            with open(p, "w", encoding="utf-8") as f:
                f.write('{"floor_pct": 0.35, "outside_assets_usd": 800000}')
            cfg = pw.load_config(p)
            self.assertEqual((cfg["floor_pct"], cfg["outside_assets_usd"], cfg["symbol"]), (0.35, 800000, "INTC"))


if __name__ == "__main__":
    unittest.main()
