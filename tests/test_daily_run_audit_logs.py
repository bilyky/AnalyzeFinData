"""Audit lines and behaviour edges of the daily run (R&D #78 S1, ADR 0005).

The daily-run golden (tests/test_scenario_runner.py) pins behaviour: saved state,
transactions, decisions, DNA, emails, covered-call calls and exceptions. It no longer pins
the whole logger stream, so moving code or fixing a doubled log line doesn't churn it.

This module keeps what the logger stream used to guard:

* **Audit lines** the project rules require: every abort, rejection and forced exit is
  logged with its reason. Asserted by level and meaning, not by the exact full stream.
* **Behaviour edges** that the golden fixtures don't reach. A golden-only mutant sweep
  (scripts/utils/mutate_scenario_steps.py --modules ...) showed 8 mutants that only the log
  stream caught: the cash buffer, the s10 floor's cash scale, the R:R reject, the same-day
  scale-in guard's SELL check, the stop ratchet never lowering, the abort log, the
  cash-deployment gate and the pre-flight heal scope. Each has a test here, so the
  behaviour golden plus this module catch at least what the full-log golden did.

Every case runs the real game entry point under the same patched world as the golden
(tests/test_scenario_runner._Run). Nothing touches the network or the real Data folder.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ai_portfolio_game as game  # noqa: E402
from tests.test_scenario_runner import _TODAY, _Run, _row  # noqa: E402


def _run(**kw):
    run = _Run(**kw)
    out = run(game.run_daily_ai_management)
    return run, out


def _lines(out, level=None):
    """Logger calls as (level, message) for the given level (all levels if None)."""
    return [(name, str(args[0])) for name, args in out["log"] if args and (level is None or name == level)]


def _has(out, level, text):
    return any(text in msg for _, msg in _lines(out, level))


def _buys(out):
    return {(t["type"], t["symbol"]) for t in (out["txs"] or []) if t["type"] in ("BUY", "BUY_SCALE_IN")}


class TestAuditLines(unittest.TestCase):
    """The run says why it stopped, rejected or sold, at the right level."""

    def test_market_closed_abort_is_logged_and_nothing_is_saved(self):
        _, out = _run(market_open=False, force=False)
        self.assertTrue(_has(out, "info", "Aborting AI Move: closed (test)"))
        self.assertTrue(_has(out, "info", "AI Management aborted before state initialization"))
        self.assertEqual(out["save_calls"], 0)

    def test_unexpected_error_is_logged_as_exception(self):
        _, out = _run(load_error=ValueError("corrupt game file"))
        self.assertTrue(_has(out, "exception", "run_daily_ai_management failed: corrupt game file"))
        self.assertTrue(_has(out, "info", "AI Management aborted before state initialization"))
        self.assertIsNone(out["raised"])

    def test_missing_workbook_defers_the_run(self):
        _, out = _run(exists=False)
        self.assertTrue(_has(out, "info", "Workbook not found. AI Management deferred."))

    def test_stale_workbook_is_refused_as_an_error(self):
        _, out = _run(stale="workbook is from yesterday")
        self.assertTrue(_has(out, "error", "Refusing to trade on a stale workbook: workbook is from yesterday"))
        self.assertEqual(len(out["emails"]), 1)

    def test_no_prices_raises_the_critical_data_failure(self):
        _, out = _run(prices={})
        self.assertEqual(out["raised"][0], "RuntimeError")
        self.assertIn("Critical Data Failure", out["raised"][1])

    def test_full_run_logs_each_rejection_with_its_reason(self):
        _, out = _run()
        warnings = [m for _, m in _lines(out, "warning")]
        for text in ("AI BUY REJECTED (CNXC Trap): GAP", "AI BUY REJECTED (Risk-Reward Gate): LOWRR",
                     "AI BUY REJECTED (Momentum Floor): FLOOR"):
            with self.subTest(text=text):
                self.assertTrue(any(text in m for m in warnings), text)

    def test_full_run_logs_the_profile_the_queued_fill_and_the_stop_exit(self):
        _, out = _run()
        self.assertTrue(_has(out, "info", "AI ACTIVE STRATEGY: BALANCED (Manual Override - Locked)"))
        self.assertTrue(_has(out, "info", "AI QUEUED BUY EXECUTED:"))
        self.assertTrue(_has(out, "info", "AI LIVE SELL: LOSE"))

    def test_ai_override_and_ignored_verdict_are_logged(self):
        extra = {
            "positions": {
                "FADE": {"qty": 20, "cost": 90.0, "stop_loss": 80.0, "highest_close_since_acq": 106.0,
                         "shadow_verdict": "HOLD"},
                "SLIP": {"qty": 20, "cost": 90.0, "stop_loss": 85.0, "highest_close_since_acq": 95.0,
                         "ai_verdict": "HOLD"},
            },
            "rows": [_row("FADE", 105.0, -2.0, -1.0, prev_sell=105.0), _row("SLIP", 84.0, -2.0, -1.0, prev_sell=84.0)],
            "prices": {"FADE": 105.0, "SLIP": 84.0},
        }
        _, out = _run(extra=extra)
        self.assertTrue(_has(out, "info", "[AI EXIT OVERRIDE] FADE"))
        self.assertTrue(_has(out, "info", "SLIP: AI verdict not applied"))

    def test_each_rejected_candidate_is_rejected_once(self):
        # One reason per rejected symbol; a rejection that falls through to the next gate
        # would log a second line (the R:R reject's `continue`).
        _, out = _run()
        for sym in ("GAP", "LOWRR", "FLOOR"):
            with self.subTest(sym=sym):
                hits = [m for _, m in _lines(out, "warning") if "AI BUY REJECTED" in m and f"): {sym} " in m]
                self.assertEqual(len(hits), 1, hits)


class TestBehaviourEdges(unittest.TestCase):
    """Rules whose effect the golden fixtures don't show (they used to show only in log text)."""

    def test_cash_deployment_gate_never_downgrades_an_aggressive_regime(self):
        # The gate only upgrades DEFENSIVE to BALANCED. An AGGRESSIVE autopilot day stays AGGRESSIVE.
        _, out = _run(manual_profile=None, regime="AGGRESSIVE")
        self.assertEqual(out["saved"]["profile"], "AGGRESSIVE")

    def test_preflight_heals_only_stale_positions(self):
        run, _ = _run()  # nothing is stale in the fixture
        run.last["heal"].assert_not_called()

    def test_profit_lock_ratchet_never_lowers_a_stop(self):
        # LOCK's stop (78) is above the trail the ratchet would compute (80 - 2.5 x ATR 2 = 75).
        extra = {"positions": {"LOCK": {"qty": 10, "cost": 50.0, "stop_loss": 78.0, "highest_close_since_acq": 80.0}},
                 "rows": [_row("LOCK", 80.0, 3.0, 4.0, prev_sell=80.0)], "prices": {"LOCK": 80.0}}
        _, out = _run(extra=extra)
        self.assertEqual(out["saved"]["positions"]["LOCK"]["stop_loss"], 78.0)

    def test_cash_buffer_is_kept_out_of_buy_sizing(self):
        # $2,000 cash: each buy gets (cash - BALANCED's 10% buffer) / free slots. Spending the
        # buffer too would size NEW1 at 28 shares instead of 24.
        _, out = _run(extra={"state": {"balance": 2000.0, "queued_orders": []}})
        qty = {t["symbol"]: t["qty"] for t in out["txs"] if t["type"] == "BUY"}
        self.assertEqual(qty, {"NEW1": 24, "NEW2": 28, "QUEUED": 28})
        self.assertGreaterEqual(out["saved"]["balance"], 0.10 * out["saved"]["equity"])

    def test_s10_floor_is_relaxed_when_cash_drag_is_high(self):
        # ~70% cash is above the 25% drag threshold, so the floor is 2.0, not 2.5: MID (s10 2.2) buys.
        extra = {"rows": [_row("MID", 50.0, 2.2, 9.0, stop=47.0, target=60.0)], "prices": {"MID": 50.0}}
        _, out = _run(extra=extra)
        self.assertIn(("BUY", "MID"), _buys(out))

    def test_same_day_buy_does_not_block_a_scale_in(self):
        # The same-day guard (#181) blocks a scale-in only after a same-day SELL, not a BUY.
        history = [{"date": _TODAY, "time": "06:00:00", "type": "BUY", "symbol": "OLD", "price": 54.0, "qty": 1}]
        _, out = _run(extra={"state": {"history": history}})
        self.assertIn(("BUY_SCALE_IN", "OLD"), _buys(out))


if __name__ == "__main__":
    unittest.main()
