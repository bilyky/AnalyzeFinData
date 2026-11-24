"""PROD review 2026-11-24 fixes.

1. No momentum rotation in a run the circuit breaker froze: 11-24 07:03 the rotation sold TDC
   "to open slot" in a frozen run, a moment after winner protection held it.
2. The repair pass retries, once and at the end, a priority symbol that failed on a network error:
   11-23 17:06-17:10 read timeouts at the start of the pass failed all 9 held names and four
   reference ETFs until the next evening.
3. The covered-call writer does not warn on the Research sheet's normal layout (an empty header
   cell over the symbol column), only when that column carries another label.
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tests  # noqa: F401  hermetic harness
import ai_portfolio_game as game
import rapidapi
from aether import options
from aether.scenario.steps import rotate_positions
from tests.test_rapidapi import TODAY, _cache, _real_bar, _write


class RotationFrozenTest(unittest.TestCase):
    def _run(self, state):
        txs = []
        with mock.patch.object(game, "evaluate_momentum_rotation",
                               return_value=(["TDC"], 3, 668.16)) as rot, \
             mock.patch.object(game, "is_market_hours", return_value=True), \
             mock.patch.object(game.options, "unwind_option_liability_if_held"), \
             mock.patch.object(game, "log_closed_trade_dna"), \
             mock.patch.object(game, "_log") as log:
            slots = rotate_positions(state, {"TDC": 37.12}, "AGGRESSIVE", 2, 11, [{"sym": "X"}],
                                     {"TDC": -0.4}, "2026-11-24", "07:00:05", txs)
        return slots, txs, rot, log

    def _state(self, frozen):
        s = {"balance": 2000.0, "history": [], "positions": {"TDC": {"qty": 18, "cost": 35.31}}}
        if frozen:
            s[game.circuit_breaker.FREEZE_KEY] = "Rolling 10-day Drawdown Breach (SPY is down -5.23%)"
        return s

    def test_a_frozen_run_rotates_nothing(self):
        state = self._state(frozen=True)
        slots, txs, rot, log = self._run(state)
        self.assertEqual((slots, txs, list(state["positions"]), state["balance"]), (2, [], ["TDC"], 2000.0))
        rot.assert_not_called()
        self.assertIn("Momentum rotation skipped (buys frozen this run)", log.info.call_args.args[0])

    def test_an_unfrozen_run_still_rotates(self):
        state = self._state(frozen=False)
        slots, txs, rot, _ = self._run(state)
        self.assertEqual((slots, [t["symbol"] for t in txs], state["positions"]), (3, ["TDC"], {}))
        rot.assert_called_once()


class RepairRetryTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._orig = rapidapi.OHLCV_DIR
        rapidapi.OHLCV_DIR = self._tmp.name
        self.addCleanup(lambda: setattr(rapidapi, "OHLCV_DIR", self._orig))
        for s in ("HELD", "OTHER"):          # both 35 days stale: both need repair
            _write(self._tmp.name, s, _cache({"2026-07-01": _real_bar(10, 11, 9, 10, 1000)}, "2026-07-01"))

    def _repair(self, effects, budget=10, priority=frozenset({"HELD"})):
        calls = []

        def fetch(sym, path, outputsize="compact"):
            calls.append(sym)
            e = effects.get(sym, [])
            if e:
                err = e.pop(0)
                if err:
                    raise err

        with mock.patch.object(rapidapi, "_fetch_and_merge", side_effect=fetch), \
             mock.patch("rapidapi.time.sleep"):
            res = rapidapi.repair_missing(["HELD", "OTHER"], TODAY, max_fetches=budget, priority=set(priority))
        return calls, res

    def test_a_priority_timeout_is_retried_once_at_the_end(self):
        calls, res = self._repair({"HELD": [requests.exceptions.ReadTimeout("Read timed out"), None]})
        self.assertEqual(calls, ["HELD", "OTHER", "HELD"])
        self.assertEqual((res["updated"], res["failed_priority"], res["errors"]), (2, [], []))

    def test_a_second_failure_stays_failed(self):
        calls, res = self._repair({"HELD": [requests.exceptions.ConnectionError("x"),
                                            requests.exceptions.ConnectionError("x")]})
        self.assertEqual(calls, ["HELD", "OTHER", "HELD"])
        self.assertEqual((res["updated"], res["failed_priority"]), (1, ["HELD"]))

    def test_an_api_answer_is_not_retried(self):
        calls, res = self._repair({"HELD": [RuntimeError("Alpha Vantage error: Invalid API call")]})
        self.assertEqual(calls, ["HELD", "OTHER"])
        self.assertEqual(res["failed_priority"], ["HELD"])

    def test_a_non_priority_timeout_is_not_retried(self):
        calls, res = self._repair({"OTHER": [requests.exceptions.ReadTimeout("x")]})
        self.assertEqual(calls, ["HELD", "OTHER"])

    def test_only_as_many_retries_as_the_budget_has_room_for(self):
        _write(self._tmp.name, "HELD2", _cache({"2026-07-01": _real_bar(10, 11, 9, 10, 1000)}, "2026-07-01"))
        calls = []

        def fetch(sym, path, outputsize="compact"):
            calls.append(sym)
            if sym in ("HELD", "HELD2") and calls.count(sym) == 1:
                raise requests.exceptions.ReadTimeout("x")

        with mock.patch.object(rapidapi, "_fetch_and_merge", side_effect=fetch), \
             mock.patch("rapidapi.time.sleep"):
            res = rapidapi.repair_missing(["HELD", "HELD2", "OTHER"], TODAY, max_fetches=4,
                                          priority={"HELD", "HELD2"})
        self.assertEqual(calls, ["HELD", "HELD2", "OTHER", "HELD"])          # room for one retry
        self.assertEqual(res["failed_priority"], ["HELD2"])

    def test_the_retry_stays_within_the_budget(self):
        calls, res = self._repair({"HELD": [requests.exceptions.ReadTimeout("x"), None]}, budget=2)
        self.assertEqual(calls, ["HELD", "OTHER"])               # 2 calls used, no room left
        self.assertEqual(res["failed_priority"], ["HELD"])


class _Sheet:
    def __init__(self, header, rows=()):
        self._rows = [tuple(header)] + [tuple(r) for r in rows]

    def iter_rows(self, min_row=1, max_row=None, values_only=True):
        rows = self._rows[min_row - 1:max_row] if max_row else self._rows[min_row - 1:]
        return iter(rows)


class OptionHeaderTest(unittest.TestCase):
    def _warnings(self, header):
        with mock.patch.object(options, "_log") as log:
            options.execute_weekly_covered_call_pass({"positions": {}, "history": []}, "2026-11-24", {},
                                                     _Sheet(header))
        return [c.args[0] for c in log.warning.call_args_list if "'Symbol' header" in c.args[0]]

    def test_the_normal_layout_does_not_warn(self):
        header = [None] * 25 + ["Long60"]               # PROD: D1 is empty over the symbols
        self.assertEqual(self._warnings(header), [])

    def test_a_moved_layout_warns(self):
        header = ["", "", "", "Industry"] + [None] * 21 + ["Long60"]
        w = self._warnings(header)
        self.assertEqual(len(w), 1)
        self.assertIn("'industry'", w[0])


if __name__ == "__main__":
    unittest.main()
