"""A failed morning pre-flight stops the scheduled trade run's buys; a manual --force run may buy.

PROD 2026-11-09: the 05:30 pipeline aborted on its pre-flight (Chaikin session expired), yet the 07:00
trade run bought ATI and added to COMP and AU on Sunday's workbook. The stale-workbook gate passed,
because that workbook was written after Friday's close. Owner decision (PROD review 11-09): no buys
after a failed pre-flight; exits still run; a manual run (--run --force) may buy.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autonomous_pipeline as ap
from aether import circuit_breaker, preflight_status
from aether.scenario import runners, steps

TODAY = "2026-11-09"


class PreflightStatusFileTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.file = Path(self._td.name) / "preflight_status.json"
        self._p = mock.patch.object(preflight_status, "STATUS_FILE", self.file)
        self._p.start()

    def tearDown(self):
        self._p.stop()
        self._td.cleanup()

    def test_redirected_by_the_harness(self):
        self._p.stop()
        try:
            self.assertNotIn(str(Path(__file__).resolve().parents[1] / "Data"),
                             str(Path(preflight_status.STATUS_FILE).resolve()))
        finally:
            self._p.start()

    def test_a_fail_today_blocks_and_tomorrow_does_not(self):
        preflight_status.record(False, TODAY)
        self.assertTrue(preflight_status.failed_on(TODAY))
        self.assertFalse(preflight_status.failed_on("2026-11-10"))

    def test_a_pass_today_does_not_block(self):
        preflight_status.record(False, TODAY)
        preflight_status.record(True, TODAY)           # a later manual pipeline re-run passed
        self.assertFalse(preflight_status.failed_on(TODAY))

    def test_no_record_does_not_block(self):
        self.assertFalse(preflight_status.failed_on(TODAY))

    def test_an_unreadable_record_blocks(self):
        self.file.write_text("{not json", encoding="utf-8")
        self.assertTrue(preflight_status.failed_on(TODAY))

    def test_a_record_without_a_verdict_blocks(self):
        self.file.write_text(json.dumps({"date": TODAY}), encoding="utf-8")
        self.assertTrue(preflight_status.failed_on(TODAY))

    def test_record_contents(self):
        preflight_status.record(False, TODAY)
        rec = json.loads(self.file.read_text(encoding="utf-8"))
        self.assertEqual(rec["date"], TODAY)
        self.assertIs(rec["passed"], False)


class GateStageTest(unittest.TestCase):
    QUEUE = [{"type": "BUY", "symbol": "ATI", "reason": "x"}, {"type": "SELL", "symbol": "LUV", "reason": "y"}]

    def _gate(self, failed, force, state=None):
        state = state if state is not None else {}
        with mock.patch.object(preflight_status, "failed_on", return_value=failed):
            out = steps.apply_preflight_gate(state, list(self.QUEUE), TODAY, force)
        return state, out

    def test_failed_preflight_freezes_buys_and_drops_queued_buys(self):
        state, out = self._gate(True, False)
        self.assertIn("pre-flight", state[circuit_breaker.FREEZE_KEY].lower())
        self.assertEqual([q["type"] for q in out], ["SELL"])

    def test_manual_force_run_may_buy(self):
        state, out = self._gate(True, True)
        self.assertNotIn(circuit_breaker.FREEZE_KEY, state)
        self.assertEqual(out, self.QUEUE)

    def test_passed_preflight_changes_nothing(self):
        state, out = self._gate(False, False)
        self.assertNotIn(circuit_breaker.FREEZE_KEY, state)
        self.assertEqual(out, self.QUEUE)

    def test_an_active_breaker_reason_is_kept(self):
        state, _ = self._gate(True, False, {circuit_breaker.FREEZE_KEY: "Single-Day Capitulation"})
        self.assertEqual(state[circuit_breaker.FREEZE_KEY], "Single-Day Capitulation")


class RunnerWiringTest(unittest.TestCase):
    def test_runner_gates_after_pricing_and_before_queued_orders(self):
        order = []
        game = steps._pkg()
        state = {"positions": {}, "queued_orders": [], "balance": 0.0, "equity": 0.0, "history": []}
        with mock.patch.object(game, "is_market_open", return_value=(True, "")), \
             mock.patch.object(game, "load_game", return_value=state), \
             mock.patch.object(game, "get_strategy_rules", return_value={}), \
             mock.patch.object(steps, "determine_profile", return_value="BALANCED"), \
             mock.patch.object(steps, "open_research_workbook", return_value=(mock.Mock(), mock.Mock())), \
             mock.patch.object(steps, "assemble_symbol_universe", return_value={"all_syms": [], "symbols_to_check": []}), \
             mock.patch.object(steps, "price_and_settle", side_effect=lambda *a, **k: order.append("price") or {}), \
             mock.patch.object(steps, "apply_preflight_gate",
                               side_effect=lambda s, q, t, force: order.append(("gate", force)) or q), \
             mock.patch.object(steps, "execute_queued_orders", side_effect=lambda *a, **k: order.append("queued")), \
             mock.patch.object(steps, "decide_exits", return_value={}), \
             mock.patch.object(steps, "execute_exits"), \
             mock.patch.object(steps, "screen_buys", return_value={"available_slots": 0, "max_positions": 0,
                                                                   "top_buys": [], "active_position_scores": {},
                                                                   "min_cash_required": 0}), \
             mock.patch.object(steps, "rotate_positions", return_value=0), \
             mock.patch.object(steps, "deploy_capital"), \
             mock.patch.object(steps, "finalize_run"):
            runners.run_daily_management(force=True)
        self.assertEqual(order, ["price", ("gate", True), "queued"])


class PipelineRecordsVerdictTest(unittest.TestCase):
    class _Stop(Exception):
        """Raised once the pipeline gets past the pre-flight, so the test runs nothing after it."""

    def _main(self, passed):
        def log(msg, *a, **k):
            if str(msg).startswith("Starting Daily Trading Pipeline"):
                raise self._Stop
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        with mock.patch.object(ap, "PIPELINE_LOCK_FILE", Path(td.name) / "pipeline_run.lock"), \
             mock.patch.object(ap, "cleanup_orphaned_processes"), \
             mock.patch.object(ap, "atexit"), \
             mock.patch.object(ap, "DailyRunGuard"), \
             mock.patch.object(ap, "verify_data_freshness", return_value=(False, "stale")), \
             mock.patch.object(ap, "is_market_hours", return_value=False), \
             mock.patch.object(ap, "run_preflight_diagnostics", return_value=passed), \
             mock.patch.object(ap.notify, "send_email"), \
             mock.patch.object(ap, "log", side_effect=log), \
             mock.patch.object(ap.preflight_status, "record") as rec, \
             mock.patch("sys.argv", ["autonomous_pipeline.py"]):
            with self.assertRaises(SystemExit if not passed else self._Stop):
                ap.main()
        return rec

    def test_a_failed_preflight_is_recorded(self):
        rec = self._main(False)
        rec.assert_called_once()
        self.assertIs(rec.call_args.args[0], False)

    def test_a_passed_preflight_is_recorded(self):
        rec = self._main(True)
        rec.assert_called_once()
        self.assertIs(rec.call_args.args[0], True)


if __name__ == "__main__":
    unittest.main()
