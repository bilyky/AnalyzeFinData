"""The data-health buy freeze: no new buys on broken inputs (PROD 2026-08-31..09-15, when the
Chaikin API switch left 44 fields empty and the game kept buying; those 11 buys trailed SPY by
4.75 pp on average over 10 sessions). Covers the check's gate output, the remembered fields that
keep the freeze on until they fill again, and the game step."""
import os
import unittest
from unittest import mock

from aether import circuit_breaker
from aether import data_completeness as dc
from aether.scenario import steps
from tests.test_data_completeness import Base, _snap

QUEUE = [{"type": "BUY", "symbol": "ATI", "reason": "x"}, {"type": "SELL", "symbol": "LUV", "reason": "y"}]
FIELD = "pgr.Financials"


class TestCheckGateOutput(Base):
    def test_fields_gone_empty_are_gate_alarms_and_named(self):
        self.history()
        for s in ("AAA", "BBB", "CCC", "DDD"):
            self.write(s, "2026-09-01", _snap(factors=False))
        r = dc.check("2026-09-01", self.base)
        self.assertEqual(r["gate"], r["alarms"])
        self.assertIn(FIELD, r["dropped_fields"])
        self.assertEqual(r["fill_rates"][FIELD], 0.0)

    def test_count_drop_bad_values_and_missing_bars_gate(self):
        self.history()
        self.write("AAA", "2026-09-01", _snap(signals="xx"))
        self.bars("AAA", ["2026-08-24"])                              # no bar for 09-01
        dc._bars_cache.clear()
        text = " | ".join(dc.check("2026-09-01", self.base)["gate"])
        self.assertIn("1 snapshots, usually 4", text)
        self.assertIn("bad values", text)
        self.assertIn("no OHLCV bar", text)

    def test_raw_copy_alarm_does_not_gate(self):
        self.history(upto=7)
        os.makedirs(os.path.join(self._tmp.name, "Symbol_raw", "AAA"))
        r = dc.check("2026-09-01", self.base)
        self.assertEqual(r["alarms"], ["4 snapshots without a raw copy (Data/Symbol_raw)"])
        self.assertEqual(r["gate"], [])

    def test_quiet_day_has_an_empty_gate(self):
        self.history(upto=7)
        r = dc.check("2026-09-01", self.base)
        self.assertEqual((r["gate"], r["dropped_fields"]), ([], []))


def report(gate=(), dropped=(), rates=None):
    return {"gate": list(gate), "dropped_fields": list(dropped), "fill_rates": rates or {}}


class TestRemembered(unittest.TestCase):
    def test_outage_is_remembered_until_the_field_fills_again(self):
        state = {}
        first = steps.data_health_reasons(state, report(["44 fields went empty"], [FIELD], {FIELD: 0.0}))
        self.assertEqual(first, ["44 fields went empty"])
        self.assertEqual(state[steps.DATA_GATE_KEY], [FIELD])
        # a week later the check's own baseline has adapted: no alarm, but the field is still empty
        later = steps.data_health_reasons(state, report(rates={FIELD: 0.0}))
        self.assertEqual(len(later), 1)
        self.assertIn("still empty since their outage", later[0])
        # filled again: freeze lifts and the memory is cleared
        self.assertEqual(steps.data_health_reasons(state, report(rates={FIELD: dc.USUAL_FILL})), [])
        self.assertNotIn(steps.DATA_GATE_KEY, state)

    def test_a_clean_day_without_memory_is_fine(self):
        state = {}
        self.assertEqual(steps.data_health_reasons(state, report(rates={FIELD: 1.0})), [])
        self.assertEqual(state, {})


class TestGateStep(unittest.TestCase):
    def run_gate(self, rep=None, force=False, state=None, exc=None):
        state = state if state is not None else {}
        kw = {"side_effect": exc} if exc else {"return_value": rep}
        with mock.patch.object(steps.data_completeness, "check", **kw):
            out = steps.apply_data_health_gate(state, list(QUEUE), force)
        return state, out

    def test_broken_inputs_freeze_buys_and_drop_queued_buys(self):
        state, out = self.run_gate(report(["44 fields went empty"], [FIELD]))
        self.assertIn("Data-health freeze", state[circuit_breaker.FREEZE_KEY])
        self.assertEqual([q["type"] for q in out], ["SELL"])

    def test_sound_inputs_change_nothing(self):
        state, out = self.run_gate(report())
        self.assertNotIn(circuit_breaker.FREEZE_KEY, state)
        self.assertEqual(out, QUEUE)

    def test_a_failed_check_freezes(self):
        state, out = self.run_gate(exc=FileNotFoundError("Data/Symbol"))
        self.assertIn("check failed", state[circuit_breaker.FREEZE_KEY])
        self.assertEqual([q["type"] for q in out], ["SELL"])

    def test_force_bypasses_with_a_warning(self):
        with self.assertLogs("aether.ai_game", level="WARNING") as logs:
            state, out = self.run_gate(report(["x went empty"], [FIELD]), force=True)
        self.assertNotIn(circuit_breaker.FREEZE_KEY, state)
        self.assertEqual(out, QUEUE)
        self.assertTrue(any("[Data-Health Gate] bypassed by --force" in m for m in logs.output))

    def test_an_active_breaker_reason_is_kept(self):
        state, _ = self.run_gate(report(["x went empty"], [FIELD]),
                                 state={circuit_breaker.FREEZE_KEY: "Single-Day Capitulation"})
        self.assertEqual(state[circuit_breaker.FREEZE_KEY], "Single-Day Capitulation")


if __name__ == "__main__":
    unittest.main()
