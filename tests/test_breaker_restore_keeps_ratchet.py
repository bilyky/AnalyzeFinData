"""Breaker recovery must not undo a stop raised while the breaker was active.

On recovery, ``enforce_circuit_breaker`` puts back the stop saved before it tightened. A profit-lock or
breakeven ratchet set after the tightening is a newer, higher stop; restoring the old one would loosen
it (PROD review 2026-11-09).
"""
import unittest
from unittest import mock

from aether import circuit_breaker as cb


def _run(state, active, prices, atr=2.0):
    reason = "Single-Day Capitulation (SPY fell -3.38% in a single session)." if active else ""
    with mock.patch.object(cb, "check_systemic_risk", return_value=(active, reason)), \
         mock.patch.object(cb, "log_circuit_breaker_trigger_dna"), \
         mock.patch.object(cb, "load_spy_history", return_value=[]), \
         mock.patch.object(cb.risk_utils, "calculate_atr", return_value=atr):
        return cb.enforce_circuit_breaker(state, prices=prices)


class BreakerRestoreTest(unittest.TestCase):
    def _state(self):
        return {"positions": {"LUV": {"qty": 34, "cost": 42.0, "stop_loss": 40.0, "is_scarcity": False}},
                "queued_orders": []}

    def test_untouched_tightened_stop_is_restored(self):
        state = self._state()
        self.assertEqual(_run(state, True, {"LUV": 45.0}), ["LUV"])
        self.assertEqual(state["positions"]["LUV"]["stop_loss"], 43.0)   # 45 - 1xATR
        _run(state, False, {"LUV": 45.0})
        pos = state["positions"]["LUV"]
        self.assertEqual(pos["stop_loss"], 40.0)
        self.assertNotIn("original_stop_loss", pos)
        self.assertNotIn("breaker_stop_loss", pos)

    def test_ratchet_after_tightening_is_kept(self):
        state = self._state()
        _run(state, True, {"LUV": 45.0})
        state["positions"]["LUV"]["stop_loss"] = 44.0                     # profit-lock raised it
        _run(state, False, {"LUV": 46.0})
        pos = state["positions"]["LUV"]
        self.assertEqual(pos["stop_loss"], 44.0)
        self.assertNotIn("original_stop_loss", pos)
        self.assertNotIn("breaker_stop_loss", pos)

    def test_second_breaker_day_tracks_the_latest_tightened_stop(self):
        state = self._state()
        _run(state, True, {"LUV": 45.0})                                  # 43.0
        _run(state, True, {"LUV": 47.0})                                  # 45.0, original still 40.0
        self.assertEqual(state["positions"]["LUV"]["original_stop_loss"], 40.0)
        _run(state, False, {"LUV": 47.0})
        self.assertEqual(state["positions"]["LUV"]["stop_loss"], 40.0)


if __name__ == "__main__":
    unittest.main()
