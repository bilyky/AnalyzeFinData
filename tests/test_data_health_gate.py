"""The data-health buy freeze: no new buys on broken inputs (PROD 2026-08-31..09-15, when the
Chaikin API switch left 44 fields empty and the game kept buying; those 11 buys trailed SPY by
4.75 pp on average over 10 sessions). Covers the check's gate output, the remembered fields that
keep the freeze on until they fill again, and the game step."""
import datetime
import json
import os
import tempfile
import unittest
from unittest import mock

import daily_task
from aether import circuit_breaker
from aether import data_completeness as dc
from aether.scenario import steps
from tests.test_data_completeness import DAYS, Base, _snap

QUEUE = [{"type": "BUY", "symbol": "ATI", "reason": "x"}, {"type": "SELL", "symbol": "LUV", "reason": "y"}]
FIELD = "pgr.Financials"


class TestCheckGateOutput(Base):
    def test_fields_gone_empty_are_gate_alarms_and_named(self):
        self.history()
        for s in ("AAA", "BBB", "CCC", "DDD"):
            self.write(s, "2026-09-01", _snap(factors=False))
        r = dc.check("2026-09-01", self.base)
        self.assertEqual(len(r["gate"]), 1)
        self.assertIn("game-input fields went empty", r["gate"][0])
        self.assertIn(FIELD, r["dropped_fields"])
        self.assertEqual(r["fill_rates"][FIELD], 0.0)

    def test_a_field_the_game_does_not_read_alarms_but_does_not_gate(self):
        # 2026-09-01: nine fields such as pgr.power_bar went empty for good and nothing reads them
        for s in ("AAA", "BBB", "CCC", "DDD"):
            self.bars(s, DAYS)
            for d in DAYS[:6]:
                snap = _snap()
                snap["pgr"].append({"power_bar": 3})
                self.write(s, d, snap)
            self.write(s, "2026-09-01", _snap())
        r = dc.check("2026-09-01", self.base)
        self.assertIn("pgr.power_bar 0%", r["alarms"][0])
        self.assertEqual((r["gate"], r["dropped_fields"]), ([], []))

    def test_game_input_list(self):
        self.assertTrue(dc.is_game_input("pgr.Financials.LT Debt to Equity"))
        self.assertTrue(dc.is_game_input("checklist.strengthCount"))
        self.assertFalse(dc.is_game_input("pgr.power_bar"))
        self.assertFalse(dc.is_game_input("pgr.FinancialsX"))

    def test_no_snapshots_for_the_last_session_gates_a_morning_check(self):
        self.history(upto=6)                                      # newest snapshots: 2026-08-31
        r = dc.check(None, self.base, as_of=datetime.date(2026, 9, 2))   # last session: 09-01
        self.assertIn("no snapshots for the last session 2026-09-01 (newest: 2026-08-31)", r["gate"])
        ok = dc.check(None, self.base, as_of=datetime.date(2026, 9, 1))  # last session: 08-31
        self.assertFalse(any("last session" in g for g in ok["gate"]))
        explicit = dc.check("2026-08-31", self.base, as_of=datetime.date(2026, 9, 2))
        self.assertFalse(any("last session" in g for g in explicit["gate"]))

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
        self.assertEqual(state[dc.DATA_GATE_KEY], [FIELD])
        # a week later the check's own baseline has adapted: no alarm, but the field is still empty
        later = steps.data_health_reasons(state, report(rates={FIELD: 0.0}))
        self.assertEqual(len(later), 1)
        self.assertIn("still empty since their outage", later[0])
        # filled again: freeze lifts and the memory is cleared
        self.assertEqual(steps.data_health_reasons(state, report(rates={FIELD: dc.USUAL_FILL})), [])
        self.assertNotIn(dc.DATA_GATE_KEY, state)

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


class TestReleaseAndNotice(unittest.TestCase):
    """The memory must never hold buys silently or forever (PR #288 review)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "ai_portfolio_game.json")
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"balance": 1.0, dc.DATA_GATE_KEY: [FIELD, "pgr.Earnings"]}, f)

    def tearDown(self):
        self._tmp.cleanup()

    def _state(self):
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)

    def test_frozen_alarm_names_the_fields_and_the_release_command(self):
        a = dc.frozen_alarm(self.path)
        self.assertIn("New buys frozen: 2 game-input fields", a)
        self.assertIn("--accept-fields", a)
        os.remove(self.path)
        self.assertIsNone(dc.frozen_alarm(self.path))                # unreadable: no line, no crash

    def test_accept_fields_backs_up_then_releases(self):
        r = dc.accept_fields([FIELD, "pgr.nope"], self.path, now=datetime.datetime(2026, 11, 18, 7, 0, 0))
        self.assertEqual((r["status"], r["remembered"], r["unknown"]), ("updated", ["pgr.Earnings"], ["pgr.nope"]))
        backup = os.path.join(self._tmp.name, "Backup", "20261118_070000_pre_accept_fields", "ai_portfolio_game.json")
        with open(backup, encoding="utf-8") as f:
            self.assertEqual(json.load(f)[dc.DATA_GATE_KEY], [FIELD, "pgr.Earnings"])   # the old file, intact
        self.assertEqual(self._state()[dc.DATA_GATE_KEY], ["pgr.Earnings"])
        dc.accept_fields(["pgr.Earnings"], self.path)
        self.assertNotIn(dc.DATA_GATE_KEY, self._state())
        self.assertEqual(self._state()["balance"], 1.0)
        self.assertEqual(dc.accept_fields(["x"], self.path)["status"], "unchanged")

    def test_cli_accept_fields(self):
        with mock.patch.object(dc, "game_file", return_value=self.path), mock.patch.object(dc.sys, "stdout"):
            self.assertEqual(dc.main(["--accept-fields", FIELD]), 0)
        self.assertEqual(self._state()[dc.DATA_GATE_KEY], ["pgr.Earnings"])

    def test_evening_banner_shows_the_freeze(self):
        clean = {"day": "2026-11-16", "alarms": [], "notes": []}
        with mock.patch.object(daily_task.data_completeness, "check", return_value=clean),                 mock.patch.object(daily_task, "get_symbols_from_xls", return_value=[]),                 mock.patch.object(daily_task.data_completeness, "game_file", return_value=self.path):
            html = daily_task._data_completeness_banner("2026-11-16")
        self.assertIn("New buys frozen", html)


class TestOutageReplay(Base):
    """09-01: game fields and an unused field go empty; the game fields come back, the unused one
    never does. The freeze must hold while game fields are empty and lift when they return."""

    def _day(self, d, factors, extra):
        for s in ("AAA", "BBB", "CCC", "DDD"):
            snap = _snap(factors=factors)
            if extra:
                snap["pgr"].append({"power_bar": 3})
            self.write(s, d, snap)

    def test_freeze_lifts_when_the_game_fields_return(self):
        for s in ("AAA", "BBB", "CCC", "DDD"):
            self.bars(s, DAYS + ["2026-09-02", "2026-09-03"])
        for d in DAYS[:6]:
            self._day(d, True, True)
        state = {}
        self._day("2026-09-01", False, False)                  # outage: factors and power_bar empty
        self.assertTrue(steps.data_health_reasons(state, dc.check("2026-09-01", self.base)))
        self._day("2026-09-02", False, False)
        self.assertTrue(steps.data_health_reasons(state, dc.check("2026-09-02", self.base)))
        self._day("2026-09-03", True, False)                   # factors back, power_bar gone for good
        self.assertEqual(steps.data_health_reasons(state, dc.check("2026-09-03", self.base)), [])
        self.assertNotIn(dc.DATA_GATE_KEY, state)


if __name__ == "__main__":
    unittest.main()
