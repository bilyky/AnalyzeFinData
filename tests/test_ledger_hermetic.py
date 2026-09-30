"""The test harness must keep every learning-ledger write out of the repo's Data/."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import aether.circuit_breaker as circuit_breaker
import aether.decision_eval as decision_eval
import retrospective_analyzer

_REPO_DATA = (Path(__file__).resolve().parent.parent / "Data").resolve()


class TestLedgerPathsRedirected(unittest.TestCase):
    def test_no_ledger_path_resolves_into_repo_data(self):
        for name, path in [("decision_eval.LOG", decision_eval.LOG),
                           ("circuit_breaker.DNA_FILE", circuit_breaker.DNA_FILE),
                           ("retrospective_analyzer.RULES_FILE", retrospective_analyzer.RULES_FILE),
                           ("retrospective_analyzer.REPORT_FILE", retrospective_analyzer.REPORT_FILE)]:
            with self.subTest(name=name):
                self.assertNotEqual(Path(path).resolve().parent, _REPO_DATA, f"{name} -> {path}")

    def test_default_log_path_is_resolved_at_call_time(self):
        # A default bound at def time would write to the import-time path, not here.
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "decision_log.jsonl"
            with mock.patch.object(decision_eval, "LOG", target):
                decision_eval.log_decisions([{"symbol": "ZZZ", "rules_action": "HOLD"}])
            self.assertEqual([e["symbol"] for e in decision_eval.read_log(target)], ["ZZZ"])


if __name__ == "__main__":
    unittest.main()
