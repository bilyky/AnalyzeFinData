"""The weekly pattern-discovery run proposes failure-DNA rules; it never sets the live ones.

retrospective_analyzer.py writes Data/failure_dna_rules.json, which the game reads to reject buys
(ai_portfolio_game.check_failure_rules). pattern_discovery.py ran it in Phase 7 with no flag, so the
new AETHER_PatternDiscovery task (#274) would have rewritten the live rules every Saturday. Phase 7
now runs it with --report-only: the rules go to Data/failure_dna_rules_proposed.json and the report
says how they differ from the live rules. A run by hand (no flag) still sets them.

Also: the analyzer writes the z-score rule as "greater_equal_2.5", but the game matched only
"greater_than_2.5", so that rule never fired.
"""
import ast
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
import ai_portfolio_game as game
import rd_pattern_discovery as rd
import retrospective_analyzer as ra
from aether import ai_step, ledgers

_REPO = Path(__file__).resolve().parent.parent


def _trade(pgr, pnl, z=0.0, score=8.0):
    return {"symbol": "X", "buy_date": "2026-10-01", "buy_price": 10, "sell_date": "2026-10-05", "sell_price": 10,
            "pnl_pct": pnl, "holding_days": 4, "buy_dna": {"pgr": pgr, "score": score, "z_score": z, "industry": "I"}}


class _Ledgers(unittest.TestCase):
    """The harness (tests/__init__.py) points the ledgers at a temp folder; start each test clean."""

    def setUp(self):
        for f in (ledgers.TRADE_DNA_FILE, ledgers.FAILURE_RULES_FILE, ledgers.FAILURE_RULES_PROPOSED_FILE,
                  ledgers.RETRO_REPORT_FILE):
            if f.exists():
                f.unlink()
        self.assertNotEqual(ledgers.FAILURE_RULES_FILE.parent.name, "Data")    # never the real Data folder
        trades = [_trade("Be", -5), _trade("Be-", -3), _trade("Be", 2), _trade("Bu", 4)]   # 2 of 3 Bearish lost
        ledgers.TRADE_DNA_FILE.write_text(json.dumps(trades), encoding="utf-8")
        self.live = [{"id": "TOXIC_LOW_SCORE", "field": "score", "condition": "less_than_5.0", "reason": "r"}]
        ledgers.FAILURE_RULES_FILE.write_text(json.dumps(self.live), encoding="utf-8")


class AnalyzerTest(_Ledgers):
    def test_report_only_keeps_the_live_rules(self):
        ra.analyze(report_only=True)
        self.assertEqual(json.loads(ledgers.FAILURE_RULES_FILE.read_text(encoding="utf-8")), self.live)
        proposed = json.loads(ledgers.FAILURE_RULES_PROPOSED_FILE.read_text(encoding="utf-8"))
        self.assertEqual([r["id"] for r in proposed], ["TOXIC_BEARISH_PGR"])
        report = ledgers.RETRO_REPORT_FILE.read_text(encoding="utf-8")
        self.assertIn("REPORT ONLY: the live rules (failure_dna_rules.json) were not changed", report)
        self.assertIn("+ would add TOXIC_BEARISH_PGR: pgr startswith_Be", report)
        self.assertIn("- would remove TOXIC_LOW_SCORE: score less_than_5.0", report)

    def test_a_run_by_hand_sets_the_live_rules(self):
        ra.analyze()
        self.assertEqual([r["id"] for r in json.loads(ledgers.FAILURE_RULES_FILE.read_text(encoding="utf-8"))],
                         ["TOXIC_BEARISH_PGR"])
        self.assertFalse(ledgers.FAILURE_RULES_PROPOSED_FILE.exists())
        self.assertIn("The live rules were set", ledgers.RETRO_REPORT_FILE.read_text(encoding="utf-8"))

    def test_rules_diff(self):
        a = {"id": "A", "field": "f", "condition": "c1"}
        self.assertEqual(ra.rules_diff([a], [dict(a)]), ["  = the proposed rules match the live rules"])
        self.assertEqual(ra.rules_diff([a], [dict(a, condition="c2")]), ["  ~ would change A: c1 -> c2"])

    def test_the_cli_flag(self):
        with mock.patch.object(ra, "analyze") as an, mock.patch.object(sys, "argv", ["x", "--report-only"]):
            exec(compile(ast.parse(_main_block()), "retrospective_analyzer", "exec"), {**vars(ra), "__name__": "__main__"})
        an.assert_called_once_with(report_only=True)


def _main_block() -> str:
    """The analyzer's `if __name__ == "__main__":` block, to run its argument parsing in-process."""
    src = (_REPO / "retrospective_analyzer.py").read_text(encoding="utf-8")
    return src[src.index('if __name__ == "__main__":'):]


class PhaseSevenTest(unittest.TestCase):
    def test_pattern_discovery_runs_the_analyzer_report_only(self):
        tree = ast.parse((_REPO / "scripts" / "backtesting" / "pattern_discovery.py").read_text(encoding="utf-8"))
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "run"
                 and n.args and isinstance(n.args[0], ast.List)
                 and any(isinstance(e, ast.Name) and e.id == "retro_path" for e in n.args[0].elts)]
        self.assertEqual(len(calls), 1)
        self.assertIn("--report-only", [e.value for e in calls[0].args[0].elts if isinstance(e, ast.Constant)])

    def test_pattern_discovery_accepts_no_email(self):
        r = subprocess.run([sys.executable, str(_REPO / "scripts" / "backtesting" / "pattern_discovery.py"), "--help"],
                           capture_output=True, text=True, timeout=120, cwd=str(_REPO))
        self.assertEqual(r.returncode, 0, r.stderr[-500:])
        self.assertIn("--no-email", r.stdout)


class WeeklyJobTest(_Ledgers):
    def _run(self, report_text, mtime_offset=0.0):
        out = Path(ledgers.FAILURE_RULES_FILE.parent)

        def compute(cmd, **kw):
            (out / f"pattern_discovery_{cmd[cmd.index('--date') + 1]}.json").write_text(
                json.dumps({"s10_analysis": {}, "l60_analysis": {}}), encoding="utf-8")
            ledgers.RETRO_REPORT_FILE.write_text(report_text, encoding="utf-8")
            if mtime_offset:
                t = os.path.getmtime(ledgers.RETRO_REPORT_FILE) + mtime_offset
                os.utime(ledgers.RETRO_REPORT_FILE, (t, t))
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        ok = lambda name, system, user, **k: ai_step.StepResult("FINDINGS", "groq", None)
        with mock.patch.object(rd, "OUT_DIR", out), mock.patch.object(rd.subprocess, "run", side_effect=compute) as sp, \
                mock.patch.object(rd.ai_step, "run", side_effect=ok), mock.patch.object(rd.notify, "send_email") as send, \
                mock.patch.object(sys, "argv", ["rd_pattern_discovery.py", "--date", "2026-10-22"]):
            self.assertEqual(rd.main(), 0)
        return sp.call_args.args[0], send.call_args.args[1]

    def test_one_email_with_the_proposed_rules(self):
        cmd, body = self._run("x\n\nRULES (proposed vs live):\n  + would add TOXIC_BEARISH_PGR: pgr startswith_Be\n\nmore")
        self.assertIn("--no-email", cmd)                       # pattern_discovery.py sends no second email
        self.assertIn("Failure-DNA RULES (proposed vs live):\n  + would add TOXIC_BEARISH_PGR", body)
        self.assertNotIn("more", body.split("FINDINGS")[0])

    def test_an_old_report_is_not_quoted(self):
        _, body = self._run("RULES (proposed vs live):\n  + would add OLD\n", mtime_offset=-3600)
        self.assertNotIn("OLD", body)


class ZScoreRuleTest(unittest.TestCase):
    def _toxic(self, condition, z):
        rules = [{"id": "TOXIC_HIGH_Z_SCORE", "field": "z_score", "condition": condition, "reason": "bubble"}]
        ledgers.FAILURE_RULES_FILE.write_text(json.dumps(rules), encoding="utf-8")
        with mock.patch.object(game, "_load_symbol_today_cache", return_value=None):
            return game.check_failure_rules("X", "N", 8.0, z, "I")[0]

    def test_the_analyzers_condition_name_matches(self):
        self.assertEqual([self._toxic("greater_equal_2.5", z) for z in (2.4, 2.5, 3.0)], [False, True, True])
        self.assertEqual([self._toxic("greater_than_2.5", z) for z in (2.5, 2.6)], [False, True])   # legacy files


if __name__ == "__main__":
    unittest.main()
