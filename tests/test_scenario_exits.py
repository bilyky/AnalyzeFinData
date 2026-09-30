"""B6 tests for aether.scenario.steps.decide_exits.

``decide_exits`` is the fifth stateful stage lifted out of
``run_daily_ai_management`` — the ``# SELL logic — unified deterministic exit
policy`` loop that runs after :func:`execute_queued_orders` and before the live
SELL execution loop. These tests pin the behaviours it carries over from the
root, unchanged:

1. **Routing** — a ``SELL`` is returned (``{sym: exit_reason}``, #136) in market hours and
   queued (deduped) after hours; ``REVIEW`` / ``HOLD`` sell nothing; every entry
   is logged once via ``decision_eval.log_decisions`` (skipped when empty).
2. **Research-sheet read** — SELL-side prev-close is ``row[8]`` (not ``row[10]``),
   falling back to cost when the row is absent or non-numeric; the price falls
   back to cost when unquoted.
3. **Profit-Lock ratchet / Breakeven lock** — trails up past 1.0x ATR (scarcity
   1.5x, profile multiplier, default 2.5), never lowers, lifts to cost past 1.5x
   ATR, tracks ``highest_close_since_acq``; no ATR ⇒ no ratchet.
4. **Bank-As-You-Go scale-out** — partial sale sized off the original lot,
   keeps >= 1 share, advances ``banked_pct``, records the tx; market-hours only;
   the high-conviction hold is logged.
5. **Gap guard** (frozen ⇒ ``stop_loss=None``), lazy ``_sma50``, and the **AI
   override** precedence (real-time → stored key → stored ``verdicts``).
6. **Call-time seam** (``_pkg()``) and the package re-export.

Every collaborator is patched on the live module and ``ws`` is a hermetic fake —
no network or disk.
"""
import os
import sys
import unittest
from contextlib import ExitStack
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ai_portfolio_game as game  # noqa: E402
from aether.scenario import decide_exits as decide_reexport  # noqa: E402
from aether.scenario.steps import decide_exits  # noqa: E402


_TODAY = "2026-09-29"
_NOW = "10:00:00"
_RULES = {"atr_multiplier": 2.0}


class _FakeWS:
    """Minimal openpyxl worksheet stand-in: ``iter_rows`` yields row tuples."""

    def __init__(self, rows):
        self._rows = rows

    def iter_rows(self, min_row=2, values_only=True):
        return list(self._rows)


def _row(sym, s10=1.0, l60=2.0, prev_sell=None, prev_buy=None):
    """A Research-sheet row: sym [3], prev-close SELL [8] / BUY [10], s10 [24], l60 [25]."""
    r = [None] * 26
    r[3] = sym
    r[8] = prev_sell
    r[10] = prev_buy
    r[24] = s10
    r[25] = l60
    return tuple(r)


def _state(**pos):
    base = {"qty": 10, "cost": 100.0, "stop_loss": 90.0}
    base.update(pos)
    return {"balance": 1000.0, "positions": {"AAA": base}, "history": [], "queued_orders": []}


class _Harness:
    """Patch every collaborator decide_exits resolves via _pkg()."""

    def __init__(self, *, action="HOLD", reason="r", verdicts=None, atr=None,
                 market=True, gap=False, soft=False, plan=(0.0, "none")):
        self.log = mock.MagicMock()
        self.build = mock.MagicMock(side_effect=lambda **kw: {
            "rules_action": action, "rules_reason": reason, "verdicts": dict(verdicts or {})})
        self.log_decisions = mock.MagicMock()
        self.atr = mock.MagicMock(return_value=atr)
        self.plan = mock.MagicMock(return_value=plan)
        self.market = mock.MagicMock(return_value=market)
        self.gap = mock.MagicMock(return_value=gap)
        self.soft = mock.MagicMock(return_value=soft)
        self.sma = mock.MagicMock(return_value=95.0)

    def __enter__(self):
        self._stack = ExitStack()
        for ctx in (
            mock.patch.object(game, "_log", self.log),
            mock.patch.object(game.decision_eval, "build_entry", self.build),
            mock.patch.object(game.decision_eval, "log_decisions", self.log_decisions),
            mock.patch.object(game.risk_utils, "calculate_atr", self.atr),
            mock.patch.object(game.risk_utils, "scale_out_plan", self.plan),
            mock.patch.object(game, "is_market_hours", self.market),
            mock.patch.object(game.circuit_breaker, "is_single_stock_gap_frozen", self.gap),
            mock.patch.object(game.sell_rules, "soft_exit", self.soft),
            mock.patch.object(game, "_sma50", self.sma),
        ):
            self._stack.enter_context(ctx)
        return self

    def __exit__(self, *exc):
        return self._stack.__exit__(*exc)

    def logged(self):
        return " | ".join(str(c.args[0]) for c in self.log.info.call_args_list)


def _run(state, prices=None, rows=(), rules=_RULES, txs=None):
    txs = [] if txs is None else txs
    out = decide_exits(state, {"AAA": 100.0} if prices is None else prices,
                       rules, _FakeWS(rows), _TODAY, _NOW, txs)
    return out, txs


class TestRouting(unittest.TestCase):
    def test_no_positions_returns_empty_and_skips_log(self):
        state = {"positions": {}, "history": [], "queued_orders": []}
        with _Harness() as h:
            out, _ = _run(state)
        self.assertEqual(out, {})
        h.log_decisions.assert_not_called()

    def test_sell_in_market_hours_is_returned_not_queued(self):
        state = _state()
        with _Harness(action="SELL") as h:
            out, _ = _run(state)
        self.assertEqual(out, {"AAA": "r"})
        self.assertEqual(state["queued_orders"], [])
        self.assertIn("AAA", state["positions"])  # liquidation is the caller's job
        h.log_decisions.assert_called_once()
        self.assertEqual(len(h.log_decisions.call_args.args[0]), 1)

    def test_market_hours_sell_empty_reason_defaults_to_technical_exit(self):
        # #136: the returned dict carries the exit reason recorded on the SELL tx.
        with _Harness(action="SELL", reason=""):
            out, _ = _run(_state())
        self.assertEqual(out, {"AAA": "Technical exit"})

    def test_sell_after_hours_is_queued_with_reason(self):
        state = _state()
        with _Harness(action="SELL", reason="stop hit", market=False):
            out, _ = _run(state)
        self.assertEqual(out, {})
        self.assertEqual(state["queued_orders"], [
            {"type": "SELL", "symbol": "AAA", "reason": "Exit triggered: stop hit"}])

    def test_after_hours_sell_empty_reason_uses_default(self):
        state = _state()
        with _Harness(action="SELL", reason="", market=False):
            _run(state)
        self.assertEqual(state["queued_orders"][0]["reason"], "Exit triggered: Technical exit")

    def test_after_hours_sell_is_deduped(self):
        state = _state()
        state["queued_orders"] = [{"type": "SELL", "symbol": "AAA", "reason": "old"}]
        with _Harness(action="SELL", market=False):
            _run(state)
        self.assertEqual(state["queued_orders"], [{"type": "SELL", "symbol": "AAA", "reason": "old"}])

    def test_review_is_held_and_logged(self):
        state = _state()
        with _Harness(action="REVIEW", reason="above 50dma") as h:
            out, _ = _run(state)
        self.assertEqual(out, {})
        self.assertIn("winner-protected", h.logged())

    def test_hold_sells_nothing(self):
        state = _state()
        with _Harness(action="HOLD"):
            out, txs = _run(state)
        self.assertEqual((out, txs, state["queued_orders"]), ({}, [], []))


class TestResearchRead(unittest.TestCase):
    def test_prev_close_is_row8_not_row10(self):
        state = _state()
        with _Harness() as h:
            _run(state, rows=[_row("AAA", prev_sell=98.0, prev_buy=77.0)])
        h.gap.assert_called_once_with("AAA", 100.0, 98.0)

    def test_prev_close_falls_back_to_cost_when_row_absent(self):
        state = _state()
        with _Harness() as h:
            _run(state, rows=[_row("ZZZ", prev_sell=5.0)])
        h.gap.assert_called_once_with("AAA", 100.0, 100.0)

    def test_prev_close_falls_back_to_cost_when_non_numeric(self):
        state = _state()
        with _Harness() as h:
            _run(state, rows=[_row("AAA", prev_sell="n/a")])
        h.gap.assert_called_once_with("AAA", 100.0, 100.0)

    def test_scores_passed_to_build_entry_and_price_falls_back_to_cost(self):
        state = _state(cost=80.0)
        with _Harness() as h:
            _run(state, prices={}, rows=[_row("AAA", s10=3.5, l60=-1.0)])
        kw = h.build.call_args.kwargs
        self.assertEqual((kw["price"], kw["s10"], kw["l60"], kw["date"]), (80.0, 3.5, -1.0, _TODAY))


class TestProfitLock(unittest.TestCase):
    def test_ratchet_past_1x_atr_uses_profile_multiplier(self):
        state = _state()
        with _Harness(atr=5.0):
            _run(state, prices={"AAA": 106.0})
        pos = state["positions"]["AAA"]
        self.assertEqual(pos["stop_loss"], 96.0)
        self.assertEqual(pos["highest_close_since_acq"], 106.0)

    def test_scarcity_uses_1_5x_multiplier(self):
        state = _state(is_scarcity=True)
        with _Harness(atr=5.0):
            _run(state, prices={"AAA": 106.0})
        self.assertEqual(state["positions"]["AAA"]["stop_loss"], 98.5)

    def test_ratchet_never_lowers_stop(self):
        state = _state(stop_loss=97.0)
        with _Harness(atr=5.0):
            _run(state, prices={"AAA": 106.0})
        self.assertEqual(state["positions"]["AAA"]["stop_loss"], 97.0)

    def test_no_ratchet_below_1x_atr_but_peak_tracked(self):
        state = _state(highest_close_since_acq=120.0)
        with _Harness(atr=5.0):
            _run(state, prices={"AAA": 104.0})
        pos = state["positions"]["AAA"]
        self.assertEqual(pos["stop_loss"], 90.0)
        self.assertEqual(pos["highest_close_since_acq"], 120.0)

    def test_breakeven_lock_past_1_5x_atr_with_default_multiplier(self):
        state = _state()
        with _Harness(atr=5.0):
            _run(state, prices={"AAA": 110.0}, rules={})
        # default 2.5x: ratchet to 110-12.5=97.5, then breakeven lifts to cost 100.
        self.assertEqual(state["positions"]["AAA"]["stop_loss"], 100.0)

    def test_no_atr_means_no_ratchet(self):
        state = _state()
        with _Harness(atr=None):
            _run(state, prices={"AAA": 150.0})
        pos = state["positions"]["AAA"]
        self.assertEqual(pos["stop_loss"], 90.0)
        self.assertNotIn("highest_close_since_acq", pos)


class TestScaleOut(unittest.TestCase):
    def test_partial_sale_sized_off_original_lot(self):
        state = _state()
        with _Harness(atr=5.0, plan=(0.3, "tier1")) as h:
            _, txs = _run(state, prices={"AAA": 104.0}, rows=[_row("AAA", l60=4.0)])
        pos = state["positions"]["AAA"]
        self.assertEqual((pos["qty"], pos["banked_pct"]), (7, 0.3))
        self.assertAlmostEqual(state["balance"], 1000.0 + 3 * 104.0)
        self.assertEqual(len(txs), 1)
        self.assertEqual(state["history"], txs)
        self.assertEqual((txs[0]["qty"], txs[0]["pnl"], txs[0]["type"]), (3, 12.0, "SELL"))
        self.assertIn("Scale-out (Bank-As-You-Go): tier1", txs[0]["details"])
        h.plan.assert_called_once_with(104.0, 100.0, 5.0, 0.0, l60=4.0,
                                       l60_ceiling=game.CFG.system_covered_call_l60_ceiling)

    def test_keeps_at_least_one_share(self):
        state = _state(qty=2)
        with _Harness(atr=5.0, plan=(0.9, "tier3")):
            _run(state, prices={"AAA": 104.0})
        self.assertEqual(state["positions"]["AAA"]["qty"], 1)

    def test_high_conviction_hold_is_logged(self):
        state = _state()
        with _Harness(atr=5.0, plan=(0.0, "held: high-conviction flower")) as h:
            _, txs = _run(state, prices={"AAA": 104.0})
        self.assertEqual(txs, [])
        self.assertIn("held: high-conviction flower", h.logged())

    def test_not_planned_after_hours_or_single_share(self):
        for label, kw, pos in (("after-hours", {"market": False}, {}), ("one share", {}, {"qty": 1})):
            with self.subTest(label):
                state = _state(**pos)
                with _Harness(atr=5.0, plan=(0.3, "tier1"), **kw) as h:
                    _run(state, prices={"AAA": 104.0})
                h.plan.assert_not_called()


class TestGapAndSma(unittest.TestCase):
    def test_gap_frozen_passes_no_stop(self):
        state = _state()
        with _Harness(gap=True) as h:
            _run(state)
        self.assertIsNone(h.build.call_args.kwargs["stop_loss"])

    def test_not_frozen_passes_position_stop(self):
        state = _state()
        with _Harness() as h:
            _run(state)
        self.assertEqual(h.build.call_args.kwargs["stop_loss"], 90.0)

    def test_sma50_read_only_on_soft_signal(self):
        for soft, expected in ((False, None), (True, 95.0)):
            with self.subTest(soft=soft):
                with _Harness(soft=soft) as h:
                    _run(_state())
                self.assertEqual(h.build.call_args.kwargs["sma50"], expected)
                self.assertEqual(h.sma.called, soft)


class TestAiOverride(unittest.TestCase):
    def _entry(self, h):
        return h.log_decisions.call_args.args[0][0]

    def test_realtime_hold_downgrades_to_hold(self):
        state = _state()
        with _Harness(action="SELL", verdicts={"ai": {"verdict": "hold", "note": "n"}}) as h:
            out, _ = _run(state)
        self.assertEqual(out, {})
        self.assertEqual(self._entry(h)["rules_action"], "HOLD")
        self.assertIn("Real-time AI Shadow Heuristic (ai) returned HOLD: n", self._entry(h)["rules_reason"])

    def test_realtime_flag_downgrades_to_watch(self):
        with _Harness(action="SELL", verdicts={"ai": "FLAG-FOR-REVIEW"}) as h:
            out, _ = _run(_state())
        self.assertEqual((out, self._entry(h)["rules_action"]), ({}, "WATCH"))

    def test_stored_verdict_key_overrides(self):
        state = _state(shadow_verdict={"verdict": "HOLD", "note": "x"})
        with _Harness(action="SELL") as h:
            out, _ = _run(state)
        self.assertEqual(out, {})
        self.assertIn("Stored position shadow_verdict returned HOLD: x", self._entry(h)["rules_reason"])

    def test_stored_verdicts_dict_overrides(self):
        state = _state(verdicts={"p": "flag-for-review"})
        with _Harness(action="SELL") as h:
            out, _ = _run(state)
        self.assertEqual((out, self._entry(h)["rules_action"]), ({}, "WATCH"))

    def test_realtime_takes_precedence_over_stored(self):
        state = _state(shadow_verdict="HOLD")
        with _Harness(action="SELL", verdicts={"ai": "FLAG-FOR-REVIEW"}) as h:
            _run(state)
        self.assertEqual(self._entry(h)["rules_action"], "WATCH")

    def test_non_override_verdict_still_sells(self):
        with _Harness(action="SELL", verdicts={"ai": "SELL"}):
            out, _ = _run(_state())
        self.assertEqual(out, {"AAA": "r"})


class TestSeam(unittest.TestCase):
    def test_collaborators_resolved_at_call_time(self):
        with _Harness(action="SELL") as h:
            _run(_state())
        h.build.assert_called_once()

    def test_reexported_from_package_root(self):
        self.assertIs(decide_reexport, decide_exits)


if __name__ == "__main__":
    unittest.main()
