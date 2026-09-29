"""The test harness must keep every learning-ledger write out of the repo's Data/."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ai_portfolio_game as game
import aether.circuit_breaker as circuit_breaker
import aether.decision_eval as decision_eval

_REPO_DATA = (Path(__file__).resolve().parent.parent / "Data").resolve()


class TestLedgerPathsRedirected(unittest.TestCase):
    def test_no_ledger_path_resolves_into_repo_data(self):
        for name, path in [("decision_eval.LOG", decision_eval.LOG),
                           ("circuit_breaker.DNA_FILE", circuit_breaker.DNA_FILE),
                           ("game.TRADE_DNA_FILE", game.TRADE_DNA_FILE),
                           ("game.FAILURE_RULES_FILE", game.FAILURE_RULES_FILE)]:
            with self.subTest(name=name):
                self.assertNotEqual(Path(path).resolve().parent, _REPO_DATA, f"{name} -> {path}")

    def test_default_log_path_follows_redirect(self):
        decision_eval.log_decisions([{"symbol": "ZZZ", "rules_action": "HOLD"}])
        self.assertTrue(any(e.get("symbol") == "ZZZ" for e in decision_eval.read_log()))


if __name__ == "__main__":
    unittest.main()
