"""
Tests for data_api.read_written_calls() — the source behind GET /api/portfolio/options.

Regression for the options-tab 500: the endpoint used to iterate read_portfolio()'s
display projection, whose `positions` is a *list* (so `.items()` crashed) and whose rows
drop `written_call` entirely. The covered calls must come from the raw game state, where
`positions` is a symbol-keyed dict.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import data_api


_CALL = {"qty": 1, "strike": 110.0, "premium": 1.25,
         "expiration_date": "2026-10-16", "sigma": 0.31}


class TestReadWrittenCalls(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.game = Path(self.tmp.name) / "ai_portfolio_game.json"
        patcher = mock.patch.object(data_api, "_GAME", self.game)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def _write(self, state):
        self.game.write_text(json.dumps(state), encoding="utf-8")

    def test_only_positions_with_a_written_call_are_returned(self):
        self._write({"positions": {
            "INTC": {"qty": 100, "cost": 90.0, "price": 101.5, "written_call": _CALL},
            "AAPL": {"qty": 10, "cost": 250.0},
        }})
        self.assertEqual(data_api.read_written_calls(), [{
            "symbol": "INTC", "qty": 1, "strike": 110.0, "premium": 1.25,
            "expiration_date": "2026-10-16", "sigma": 0.31, "underlying_price": 101.5,
        }])

    def test_underlying_price_falls_back_to_cost(self):
        self._write({"positions": {"INTC": {"qty": 100, "cost": 90.0, "written_call": _CALL}}})
        self.assertEqual(data_api.read_written_calls()[0]["underlying_price"], 90.0)

    def test_does_not_source_from_the_list_shaped_display_projection(self):
        # read_portfolio() returns positions as a list with no written_call field; if the
        # options view ever reads it again it would crash or silently show no calls.
        self._write({"positions": {"INTC": {"qty": 100, "cost": 90.0, "written_call": _CALL}}})
        with mock.patch.object(data_api, "read_portfolio",
                               side_effect=AssertionError("must read raw game state")):
            self.assertEqual([r["symbol"] for r in data_api.read_written_calls()], ["INTC"])

    def test_missing_or_corrupt_state_yields_empty_list(self):
        self.assertEqual(data_api.read_written_calls(), [])
        self.game.write_text("{not json", encoding="utf-8")
        self.assertEqual(data_api.read_written_calls(), [])


if __name__ == "__main__":
    unittest.main()
