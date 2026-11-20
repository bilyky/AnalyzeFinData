"""Weekly pattern discovery: "would the game have bought it" uses the live entry gates (PROD 11-19).

The 2026-10-22 report listed all 10 top S10 winners as missed under the old test (S10+L60 >= 9.5,
the DEFENSIVE profile, plus PGR Bu/Bu+), which is not what the live game buys with. It also listed
"StrongMF+PositiveChart" as a missed-winner pattern at -5.27% (n=19) against a -3.48% universe, and
the AI analysis suggested a `--pattern` option that does not exist.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
import tests  # noqa: F401  hermetic harness
import pattern_discovery as pd
import rd_pattern_discovery as rd

_REPO = Path(__file__).resolve().parent.parent


def _row(**kw):
    r = {"symbol": "X", "s10": 3.0, "l60": 3.0, "combined": 6.0, "pgr": "N", "setup_ok": True,
         "rr": 2.5, "gain": 8.0, "money_flow": "Neutral", "chart_score": 0.0, "buying_ratio": 0.0}
    r.update(kw)
    r.setdefault("combined", r["s10"] + r["l60"])
    return r


class LiveGatesTest(unittest.TestCase):
    def test_a_name_passing_every_gate_is_bought(self):
        self.assertEqual(pd._gate_failures(_row()), [])
        self.assertTrue(pd._would_have_bought(_row()))

    def test_each_gate(self):
        cases = {
            "s10 below the floor": _row(s10=1.5, l60=5.0, combined=6.5),
            "combined below 5": _row(s10=2.5, l60=1.0, combined=3.5),
            "setup False": _row(setup_ok=False),
            "R:R below 2": _row(rr=1.2),
            "gain below 5%": _row(gain=3.0),
            "R:R unknown": _row(rr=None),
            "Bearish PGR": _row(pgr="Be-"),
        }
        for label, row in cases.items():
            with self.subTest(label):
                self.assertFalse(pd._would_have_bought(row))
                self.assertEqual(len(pd._gate_failures(row)), 1, pd._gate_failures(row))

    def test_an_elite_breakout_skips_the_rr_and_pgr_gates(self):
        """The SA shape (11-18): Bearish PGR, R:R 0.34, target 2.8%, but S10+L60 >= 8 and S10 >= 2."""
        sa = _row(s10=6.9, l60=8.7, combined=15.6, pgr="Be", rr=0.34, gain=2.8)
        self.assertEqual(pd._gate_failures(sa), [])

    def test_the_10_22_mos_row(self):
        mos = _row(symbol="MOS", s10=2.7, l60=4.6, combined=7.3, pgr="Be-", setup_ok=False, rr=None, gain=None)
        reasons = pd._rejection_reasons(mos)
        self.assertIn("setup=False", reasons)
        self.assertTrue(any(r.startswith("pgr=Be- Bearish") for r in reasons))
        self.assertFalse(any("9.5" in r for r in reasons))            # the old DEFENSIVE threshold is gone


class ComboTest(unittest.TestCase):
    def _candidates(self, combo_return):
        scores = [_row(symbol=f"C{i}", money_flow="Strong", chart_score=1.0) for i in range(6)]
        scores += [_row(symbol=f"U{i}") for i in range(20)]
        returns = {r["symbol"]: {"r10": combo_return if r["symbol"].startswith("C") else -3.0} for r in scores}
        missed = [{"symbol": "W", "fwd_r10": 20.0, "factors": {}}]
        return [c for c in pd.extract_candidate_patterns(missed, scores, returns, "r10")
                if c.get("type") == "combo_signal"]

    def test_listed_only_when_it_beats_the_universe(self):
        self.assertEqual(self._candidates(-5.27), [])                   # the 10-22 case
        got = self._candidates(4.0)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["avg_return"], 4.0)
        self.assertLess(got[0]["universe_avg"], 4.0)


class PromptTest(unittest.TestCase):
    def test_no_invented_options(self):
        self.assertIn("Never invent a command-line option", rd.analysis_instructions())
        skill = (_REPO / ".claude" / "commands" / "pattern-discover.md").read_text(encoding="utf-8")
        self.assertNotIn("{command}", skill)
        self.assertIn("pattern_discovery.py --date {DATE} --validate", skill)
        self.assertIn("no `--pattern` flag", skill)


if __name__ == "__main__":
    unittest.main()
