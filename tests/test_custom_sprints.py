"""
Tests for the custom-sprint features, exercising the REAL production helpers
(_active_setup_symbols, _execute_buys, calculate_bubble_z_score, watchdog.check_logs)
rather than copies of their logic, so a regression in the shipped code fails here.
E*TRADE / verify / ATR are mocked; no network.
"""
import os
import sys
import unittest
import tempfile
import json
import openpyxl
from unittest import mock
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import ai_portfolio_game as game
import watchdog


def _defensive_state():
    return {"balance": 7746.29, "equity": 10295.0,
            "positions": {"FANG": {"qty": 8, "cost": 184.37}, "HE": {"qty": 81, "cost": 13.57}},
            "history": [], "start_date": "2026-01-01", "profile": "DEFENSIVE"}


_RULES = {"max_positions": 3, "max_allocation_pct": 0.10, "atr_multiplier": 1.5,
          "min_score_threshold": 10.0, "cash_buffer_pct": 0.50}

_TOP_BUYS = [{"sym": "BSY", "price": 31.55, "total": 11.5, "bottom_desc": ""},
             {"sym": "APA", "price": 33.825, "total": 10.7, "bottom_desc": ""}]


class TestActiveSetupSymbols(unittest.TestCase):
    """Calls the real _active_setup_symbols filter (no row-50 cap; '1'/'OK' active)."""

    def _ws(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Research"
        ws.append(["spacer"] * 30)                 # row 1 header
        for _ in range(340):                       # deep rows, past any old row-50 slice
            ws.append([None] * 30)
        for sym, setup in [("APA", "1"), ("XYZ", "0"), ("MMM", "OK")]:
            r = [None] * 30
            r[3], r[20] = sym, setup
            ws.append(r)
        return ws

    def test_active_setups_extracted_deep_rows(self):
        syms = game._active_setup_symbols(self._ws())
        self.assertIn("APA", syms)          # setup '1'
        self.assertIn("MMM", syms)          # setup 'OK'
        self.assertNotIn("XYZ", syms)       # setup '0'
        self.assertEqual(len(syms), 2)


class TestExecuteBuys(unittest.TestCase):
    """Calls the real _execute_buys buy-slot loop."""

    def test_fallback_on_verify_failure(self):
        # BSY (top rank) fails verification -> slot falls through to APA.
        state = _defensive_state()
        new_tx = []
        def verify(sym): return (False, "Failed check") if sym == "BSY" else (True, "Verified")
        with mock.patch.object(game, "backtrack_verify", side_effect=verify), \
             mock.patch.object(game, "calculate_bubble_z_score", return_value=None), \
             mock.patch("risk_utils.calculate_atr", return_value=1.0):
            n = game._execute_buys(state, list(_TOP_BUYS), 1, state["equity"] * 0.50,
                                   _RULES, "2026-07-09", "10:00", new_tx)
        self.assertEqual(n, 1)
        self.assertIn("APA", state["positions"])
        self.assertNotIn("BSY", state["positions"])
        self.assertEqual([t["symbol"] for t in new_tx], ["APA"])
        # (7746.29 - 5147.50) / 1 // 33.825 = 76 shares
        self.assertEqual(state["positions"]["APA"]["qty"], 76)

    def test_bubble_guard_rejects_and_falls_through(self):
        # BSY trades >2.5 SD above its mean -> rejected; APA (0.9 SD) fills the slot.
        state = _defensive_state()
        new_tx = []
        def z(sym): return 3.1 if sym == "BSY" else 0.9
        with mock.patch.object(game, "calculate_bubble_z_score", side_effect=z), \
             mock.patch.object(game, "backtrack_verify", return_value=(True, "Verified")), \
             mock.patch("risk_utils.calculate_atr", return_value=1.0):
            n = game._execute_buys(state, list(_TOP_BUYS), 1, state["equity"] * 0.50,
                                   _RULES, "2026-07-09", "10:00", new_tx)
        self.assertEqual(n, 1)
        self.assertIn("APA", state["positions"])
        self.assertNotIn("BSY", state["positions"])

    def test_slot_cap_respected(self):
        # Only one slot -> only one buy even when both candidates are clean.
        state = _defensive_state()
        new_tx = []
        with mock.patch.object(game, "calculate_bubble_z_score", return_value=None), \
             mock.patch.object(game, "backtrack_verify", return_value=(True, "Verified")), \
             mock.patch("risk_utils.calculate_atr", return_value=1.0):
            n = game._execute_buys(state, list(_TOP_BUYS), 1, state["equity"] * 0.50,
                                   _RULES, "2026-07-09", "10:00", new_tx)
        self.assertEqual(n, 1)


class TestWatchdogLogScanning(unittest.TestCase):
    def test_ignore_success_with_0_errors(self):
        mock_log = "Status: [2026-07-09 05:36:24] OHLCV: 1 recovered, 514 already current, 0 errors\n"
        with mock.patch("builtins.open", mock.mock_open(read_data=mock_log)), \
             mock.patch("os.path.exists", return_value=True):
            errors = watchdog.check_logs()
        self.assertEqual(errors, [])


class TestBubbleZScoreCalculation(unittest.TestCase):
    def setUp(self):
        self._orig_symdir = game.SYMBOL_FULL_DIR
        self.temp_dir = tempfile.TemporaryDirectory()
        game.SYMBOL_FULL_DIR = Path(self.temp_dir.name) / "Data" / "Symbol_full"
        os.makedirs(game.SYMBOL_FULL_DIR, exist_ok=True)
        # 600 rising closes; zero-padded keys so sort == chronological order.
        ts = {f"{i:05d}": {"4. close": str(100.0 + i * 0.5)} for i in range(600)}
        with open(game.SYMBOL_FULL_DIR / "BUBBLE_daily.json", "w") as f:
            json.dump({"Time Series (Daily)": ts}, f)

    def tearDown(self):
        game.SYMBOL_FULL_DIR = self._orig_symdir
        self.temp_dir.cleanup()

    def test_sufficient_history_positive_z(self):
        score = game.calculate_bubble_z_score("BUBBLE")
        self.assertIsNotNone(score)
        self.assertGreater(score, 1.5)

    def test_insufficient_history_returns_none(self):
        ts = {f"{i:05d}": {"4. close": "100"} for i in range(250)}
        with open(game.SYMBOL_FULL_DIR / "SHORT_daily.json", "w") as f:
            json.dump({"Time Series (Daily)": ts}, f)
        self.assertIsNone(game.calculate_bubble_z_score("SHORT"))

    def test_missing_file_returns_none(self):
        self.assertIsNone(game.calculate_bubble_z_score("NOPE"))


if __name__ == "__main__":
    unittest.main()
