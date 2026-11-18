"""PROD review 2026-11-18: three small fixes from the 11-17 logs.

1. The weekly pattern-discovery replay of 10-22 (inside the 09-01..10-30 PGR factor gap) logged
   about 1,000 "[SCHEMA WARNING] … no PGR factors" lines: every old snapshot was schema-checked
   again. A replay now reads snapshots with check_schema=False, like the other historical reads.
2. The weekly AI prompt sent the winners list next to missed/bought, which repeat it; Groq
   refused the prompt (413 Payload Too Large).
3. A manual E*TRADE login ran in a window without AETHER_ETRADE_STORE=db, so the shared failure
   count stayed at 3. test_etrade.py now says which store it uses and warns when it isn't the
   shared one.
"""
import ast
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
import tests  # noqa: F401  hermetic harness
import powergauge
import rd_pattern_discovery as rd
from aether import etrade

_REPO = Path(__file__).resolve().parent.parent


class SchemaCheckPassThroughTest(unittest.TestCase):
    def _get(self, **kw):
        snap = {"status": "ok", "pgr": [], "metaInfo": [{"Last": 10.0}], "checklist_stocks": {"industry": "x"}}
        with mock.patch.object(powergauge.os.path, "exists", return_value=True), \
             mock.patch("builtins.open", mock.mock_open(read_data=json.dumps(snap))), \
             mock.patch.object(powergauge.PowerGauge, "init_from_json") as init:
            powergauge.get_symbol_data("ABC", "2026-10-22", prefer_cache=True, session_id={}, **kw)
        return init.call_args

    def test_default_checks_and_the_flag_skips(self):
        self.assertTrue(self._get().kwargs.get("check_schema", True))
        self.assertIs(self._get(check_schema=False).kwargs.get("check_schema"), False)

    def test_the_replay_reads_without_the_check(self):
        tree = ast.parse((_REPO / "scripts" / "backtesting" / "pattern_discovery.py").read_text(encoding="utf-8"))
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "get_symbol_data"]
        self.assertEqual(len(calls), 1)
        kw = {k.arg: k.value for k in calls[0].keywords}
        self.assertIs(getattr(kw.get("check_schema"), "value", None), False)


class AiPayloadTest(unittest.TestCase):
    def test_winners_repeating_missed_and_bought_are_dropped(self):
        a = {"symbol": "A", "r10": 9.1}
        b = {"symbol": "B", "r10": 8.0}
        report = {"s10_analysis": {"winners": [a, b], "missed": [a], "bought": [b], "universe_avg": 1}}
        out = json.loads(rd.ai_payload(report))
        self.assertNotIn("winners", out["s10_analysis"])
        self.assertEqual(out["s10_analysis"]["missed"], [a])
        self.assertIn("winners", report["s10_analysis"])                 # the saved report is untouched

    def test_a_winner_not_in_missed_or_bought_keeps_the_list(self):
        a, c = {"symbol": "A"}, {"symbol": "C"}
        out = json.loads(rd.ai_payload({"s10_analysis": {"winners": [a, c], "missed": [a], "bought": []}}))
        self.assertEqual(out["s10_analysis"]["winners"], [a, c])

    def test_the_real_shape_shrinks(self):
        """The 11-17 report shape: 10 winners, all missed, none bought."""
        rows = [{"symbol": f"S{i}", "r10": i, "reject": "score < 9.5", "factors": {"x": i}} for i in range(10)]
        report = {"s10_analysis": {"winners": rows, "missed": rows, "bought": [], "false_positives": []}}
        self.assertLess(len(rd.ai_payload(report)), len(json.dumps(report, default=str)) * 0.6)


class ManualLoginStoreNoteTest(unittest.TestCase):
    def _note(self, backend, url, env="production"):
        with mock.patch.object(etrade, "make_etrade_store", return_value=mock.Mock(backend=backend)), \
             mock.patch.object(etrade.CFG, "database_url", url):
            return etrade.manual_login_store_note(env)

    def test_warns_only_on_production_files_with_a_database(self):
        self.assertIn("NEW PowerShell window", self._note("file", "postgresql://x"))
        self.assertEqual(self._note("db", "postgresql://x"), "")
        self.assertEqual(self._note("file", ""), "")                      # no shared store here
        self.assertEqual(self._note("file", "postgresql://x", env="sandbox"), "")

    def test_the_script_prints_it_before_logging_in(self):
        src = (_REPO / "scripts" / "diagnostics" / "test_etrade.py").read_text(encoding="utf-8")
        self.assertLess(src.index("manual_login_store_note(env"), src.index("etrade.get_tokens(env=env, allow_browser=True)"))


class HarnessIgnoresTheMachineStoreTest(unittest.TestCase):
    def test_the_suite_uses_the_file_store_whatever_the_machine_sets(self):
        """A run in a shell that inherited AETHER_ETRADE_STORE=db (set for the user by #268's note)."""
        r = __import__("subprocess").run(
            [sys.executable, "-c", "import tests, os; from aether import etrade; "
             "print(os.environ.get('AETHER_ETRADE_STORE'), etrade.make_etrade_store().backend)"],
            cwd=str(_REPO), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
            env={**os.environ, "AETHER_ETRADE_STORE": "db", "DATABASE_URL": "postgresql://u:p@127.0.0.1:1/x"})
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        self.assertEqual(r.stdout.strip().splitlines()[-1], "None file")

    def test_a_live_run_keeps_them(self):
        """AETHER_LIVE_TESTS=1 is the explicit live mode; a live DB test must still see DATABASE_URL."""
        r = __import__("subprocess").run(
            [sys.executable, "-c", "import tests, os; print(os.environ.get('AETHER_ETRADE_STORE'), bool(os.environ.get('DATABASE_URL')))"],
            cwd=str(_REPO), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
            env={**os.environ, "AETHER_LIVE_TESTS": "1", "AETHER_ETRADE_STORE": "db",
                 "DATABASE_URL": "postgresql://u:p@127.0.0.1:1/x"})
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        self.assertEqual(r.stdout.strip().splitlines()[-1], "db True")


if __name__ == "__main__":
    unittest.main()
