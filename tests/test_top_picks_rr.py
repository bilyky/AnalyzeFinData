"""
Unit tests for get_top_5_picks — the dashboard "Today's Top 5 Picks" builder.

Pins two guarantees around the risk/reward number:

  R1  a qualifying pick exposes Risk_Reward == round((target-price)/(price-stop), 2),
      so the dashboard shows the SAME number the screener's fail-safe filter used
      (single source of truth — no client-side recompute);
  R2  a setup whose R:R is below the 1.5 fail-safe is excluded entirely.

Only the external boundaries are stubbed — the workbook load (openpyxl) and the
ATR/position sizing (risk_utils, which reaches the OHLCV cache). The R:R math and
the reject filter under test are the real code.
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import workbook_read


# Column layout the reader indexes in the Research sheet (0-based tuple positions).
_SYM, _IND, _PGR, _STOP, _PRICE, _TGT, _SETUP, _WIN, _S10, _L60, _PAT = \
    3, 4, 6, 9, 10, 11, 20, 23, 24, 25, 26


def _research_row(sym, price, stop, target, s10=3.0, l60=3.0, setup="OK"):
    """Build one Research-sheet row tuple (length 27) with the fields the reader reads."""
    row = [None] * 27
    row[_SYM] = sym
    row[_IND] = "Tech"
    row[_PGR] = "Bu"
    row[_STOP] = stop
    row[_PRICE] = price
    row[_TGT] = target
    row[_SETUP] = setup
    row[_WIN] = 0.6
    row[_S10] = s10
    row[_L60] = l60
    row[_PAT] = ""
    return tuple(row)


def _fake_wb(rows):
    """A stand-in for openpyxl's workbook: wb["Research"].iter_rows(...) yields `rows`."""
    ws = MagicMock()
    ws.iter_rows.return_value = iter(rows)
    wb = MagicMock()
    wb.__getitem__.return_value = ws
    return wb


class TestTopPicksRiskReward(unittest.TestCase):

    def _run(self, rows):
        with patch.object(workbook_read.openpyxl, "load_workbook", return_value=_fake_wb(rows)), \
             patch.object(workbook_read.risk_utils, "calculate_atr", return_value=2.0), \
             patch.object(workbook_read.risk_utils, "get_atr_position_size", return_value=10), \
             patch.object(workbook_read.risk_utils, "get_position_size", return_value=5):
            return workbook_read.get_top_5_picks()

    def test_qualifying_pick_exposes_risk_reward(self):
        """R1: Risk_Reward is present and equals the (target-price)/(price-stop) ratio."""
        # price 100, stop 90, target 130 → risk 10, reward 30 → R:R = 3.0
        picks = self._run([_research_row("AAA", 100.0, 90.0, 130.0)])
        self.assertEqual(len(picks), 1)
        self.assertIn("Risk_Reward", picks[0], "picks must carry the R:R the filter computed")
        self.assertEqual(picks[0]["Risk_Reward"], 3.0)

    def test_low_rr_setup_is_excluded(self):
        """R2: a setup with R:R < 1.5 is rejected by the fail-safe filter."""
        # good: R:R 3.0 (kept); weak: price 100/stop 90/target 105 → R:R 0.5 (rejected)
        picks = self._run([
            _research_row("AAA", 100.0, 90.0, 130.0),
            _research_row("WEAK", 100.0, 90.0, 105.0),
        ])
        syms = {p["Symbol"] for p in picks}
        self.assertIn("AAA", syms)
        self.assertNotIn("WEAK", syms, "R:R below 1.5 must not qualify")


if __name__ == "__main__":
    unittest.main(verbosity=2)
