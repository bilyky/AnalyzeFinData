"""An active circuit breaker freezes every buy (PROD 2026-11-06).

SPY fell 3.38% on 11-05. At 07:00:11 on 11-06 the breaker fired and tightened stops, but
enforce_circuit_breaker only cleared *queued* buys, so at 07:03 the same run bought AU and added
15 LUV. The breaker now leaves its verdict on the run's state (FREEZE_KEY); deploy_capital
skips new buys, after-hours queueing and scale-ins on it (covered calls still run), and
finalize_run never saves the key.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ai_portfolio_game as game  # noqa: E402
from aether import circuit_breaker  # noqa: E402
from aether.scenario import steps  # noqa: E402
from tests.test_scenario_deploy import _BUYS, _Harness, _run, _state  # noqa: E402

REASON = "Single-Day Capitulation (SPY fell -3.38% in a single session)."


class TestBreakerLeavesItsVerdict(unittest.TestCase):
    def test_active_sets_the_key_and_inactive_clears_it(self):
        state = {"positions": {}, "queued_orders": [{"type": "BUY", "symbol": "AU"}]}
        with mock.patch.object(circuit_breaker, "check_systemic_risk", return_value=(True, REASON)), \
             mock.patch.object(circuit_breaker, "log_circuit_breaker_trigger_dna"), \
             mock.patch.object(circuit_breaker, "load_spy_history", return_value=[]):
            circuit_breaker.enforce_circuit_breaker(state, prices={"SPY": 740.0})
        self.assertEqual(state[circuit_breaker.FREEZE_KEY], REASON)
        self.assertEqual(state["queued_orders"], [])             # the old behaviour stays
        with mock.patch.object(circuit_breaker, "check_systemic_risk", return_value=(False, "")):
            circuit_breaker.enforce_circuit_breaker(state, prices={"SPY": 760.0})
        self.assertNotIn(circuit_breaker.FREEZE_KEY, state)


class TestDeployHonoursTheFreeze(unittest.TestCase):
    def _frozen(self):
        state = _state()
        state[circuit_breaker.FREEZE_KEY] = REASON
        return state

    def test_market_hours_no_buys_no_scale_ins_but_covered_calls_run(self):
        state = self._frozen()
        with _Harness(ratio=0.0) as h:      # ratio 0: pyramiding would otherwise run
            txs = _run(state, slots=2)
        h.execute.assert_not_called()
        self.assertEqual([t for t in txs if t["type"] == "BUY_SCALE_IN"], [])
        self.assertEqual(state["positions"]["WIN"]["qty"], 10)
        h.calls.assert_called_once()
        self.assertIn("Buying frozen", str(h.log.warning.call_args))

    def test_after_hours_queues_nothing(self):
        state = self._frozen()
        with _Harness(market=(False,)):
            _run(state, slots=2)
        self.assertEqual(state["queued_orders"], [])

    def test_without_the_key_buys_and_scale_ins_run_as_before(self):
        state = _state()
        with _Harness(ratio=0.0) as h:
            txs = _run(state, slots=2)
        h.execute.assert_called_once()
        self.assertEqual(h.execute.call_args.args[1], _BUYS)
        self.assertTrue([t for t in txs if t["type"] == "BUY_SCALE_IN"])


class TestFinalizeNeverSavesTheKey(unittest.TestCase):
    def test_key_is_removed_before_save(self):
        state = {"equity": 1.0, "balance": 1.0, circuit_breaker.FREEZE_KEY: REASON}
        saved = {}
        with mock.patch.object(game, "save_game", side_effect=lambda s: saved.update(dict(s))), \
             mock.patch.object(game, "update_excel_log"), \
             mock.patch.object(game, "_log"):
            steps.finalize_run(state, None, [])
        self.assertNotIn(circuit_breaker.FREEZE_KEY, saved)
        self.assertEqual(saved["equity"], 1.0)


if __name__ == "__main__":
    unittest.main()
