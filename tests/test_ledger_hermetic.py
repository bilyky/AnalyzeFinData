"""The learning-ledger paths have one home (aether/ledgers.py), and the test harness keeps
every ledger write out of the repo's Data/."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import aether.decision_eval as decision_eval
import aether.ledgers as ledgers

_REPO = Path(__file__).resolve().parent.parent
_REPO_DATA = (_REPO / "Data").resolve()
_LEDGER_NAMES = ("trade_history_dna.json", "failure_dna_rules.json", "retrospective_report.txt")


class TestLedgerPathsRedirected(unittest.TestCase):
    def test_no_ledger_path_resolves_into_repo_data(self):
        for name, path in [("decision_eval.LOG", decision_eval.LOG),
                           ("ledgers.TRADE_DNA_FILE", ledgers.TRADE_DNA_FILE),
                           ("ledgers.FAILURE_RULES_FILE", ledgers.FAILURE_RULES_FILE),
                           ("ledgers.RETRO_REPORT_FILE", ledgers.RETRO_REPORT_FILE)]:
            with self.subTest(name=name):
                self.assertNotEqual(Path(path).resolve().parent, _REPO_DATA, f"{name} -> {path}")

    def test_default_log_path_is_resolved_at_call_time(self):
        # A default bound at def time would write to the import-time path, not here.
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "decision_log.jsonl"
            with mock.patch.object(decision_eval, "LOG", target):
                decision_eval.log_decisions([{"symbol": "ZZZ", "rules_action": "HOLD"}])
            self.assertEqual([e["symbol"] for e in decision_eval.read_log(target)], ["ZZZ"])


class TestLedgerPathsHaveOneHome(unittest.TestCase):
    def test_no_production_module_builds_its_own_ledger_path(self):
        # A second copy is how the harness redirect was bypassed before. Deliberately narrow:
        # it flags a quoted filename used as a path operand — `/ "x.json"` or `..., "x.json")`
        # — so filenames quoted in docstrings or log text don't trip it. Tracked files only,
        # so the result matches CI regardless of local worktrees/venvs/untracked scratch.
        tracked = subprocess.run(["git", "ls-files", "*.py"], cwd=_REPO, check=True,
                                 capture_output=True, text=True).stdout.splitlines()
        operands = [op for name in _LEDGER_NAMES for q in "\"'"
                    for op in (f"{q}{name}{q})", f"/ {q}{name}{q}")]
        offenders = []
        for rel in tracked:
            if rel.startswith("tests/") or rel == "aether/ledgers.py":
                continue
            text = (_REPO / rel).read_text(encoding="utf-8", errors="replace")
            for n, line in enumerate(text.splitlines(), 1):
                if any(op in line for op in operands):
                    offenders.append(f"{rel}:{n}: {line.strip()}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
