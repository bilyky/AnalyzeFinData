"""Buy rules in shadow (plan T0.4): the record, the single writer, the scorer, and the game hook (no effect)."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from aether import ledgers, rule_shadow
from tests import test_scenario_buy_screen as bs

RISING = [100.0 + 0.1 * i for i in range(300)]          # near its high, rising 200-day: no #113 reason
FALLEN = [200.0] * 250 + [150.0] * 50                    # far below its 52-week high


class TestRecord(unittest.TestCase):
    def test_a_waiver_far_below_the_high_would_be_held_back(self):
        e = rule_shadow.record("game", "XYZ", "2030-01-02", 150.0, True, FALLEN, admitted=True)
        self.assertTrue(e["admitted"])
        self.assertTrue(e["would_hold_back"])
        self.assertTrue(e["reasons"])
        self.assertEqual((e["source"], e["rule"]), ("game", "R&D #113"))

    def test_no_waiver_no_hold_back(self):
        e = rule_shadow.record("game", "XYZ", "2030-01-02", 150.0, False, FALLEN, admitted=True)
        self.assertFalse(e["would_hold_back"])
        e = rule_shadow.record("game", "XYZ", "2030-01-02", 130.0, True, RISING, admitted=True)
        self.assertFalse(e["would_hold_back"])


class TestLog(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "shadow.jsonl")

    def test_append_read_and_trim(self):
        rule_shadow.log([{"symbol": "A"}, {"symbol": "B"}], self.path)
        rule_shadow.log([{"symbol": "C"}], self.path)
        self.assertEqual([e["symbol"] for e in rule_shadow.read(self.path)], ["A", "B", "C"])
        with mock.patch.object(rule_shadow, "MAX_LINES", 2):
            rule_shadow.log([{"symbol": "D"}], self.path)
        self.assertEqual([e["symbol"] for e in rule_shadow.read(self.path)], ["C", "D"])

    def test_a_write_failure_never_raises(self):
        with mock.patch("builtins.open", side_effect=OSError("disk full")), \
                mock.patch.object(rule_shadow._log, "warning") as warn:
            rule_shadow.log([{"symbol": "A"}], self.path)
        warn.assert_called_once()

    def test_the_test_suite_writes_to_a_redirected_file(self):
        repo_data = Path(ledgers.__file__).resolve().parent.parent / "Data"
        self.assertNotEqual(Path(ledgers.RULE_SHADOW_FILE).resolve().parent, repo_data)
        self.assertEqual(Path(ledgers.RULE_SHADOW_FILE).name, "rule_shadow.jsonl")


class TestScore(unittest.TestCase):
    def test_groups_by_the_rule_verdict(self):
        days = [f"2030-01-{d:02d}" for d in range(1, 31)]
        closes = {"UP": {d: 100.0 + i for i, d in enumerate(days)}, "DOWN": {d: 100.0 - i for i, d in enumerate(days)}}
        entries = [{"symbol": "UP", "date": days[0], "would_hold_back": False},
                   {"symbol": "DOWN", "date": days[0], "would_hold_back": True},
                   {"symbol": "UP", "date": days[-2], "would_hold_back": False}]          # window not passed
        s = rule_shadow.score(entries, closes, horizons=(5,))
        self.assertEqual(s[5]["admitted_by_rule"]["n"], 1)
        self.assertAlmostEqual(s[5]["admitted_by_rule"]["mean"], 0.05)
        self.assertEqual(s[5]["would_hold_back"]["n"], 1)
        self.assertAlmostEqual(s[5]["would_hold_back"]["mean"], -0.05)


class TestGameHook(unittest.TestCase):
    def test_every_admitted_buy_gets_one_entry_and_the_decision_is_unchanged(self):
        row = bs._row(stop=95.0, target=108.0)                  # R:R 1.6: admitted only as an elite waiver
        with bs._Harness(elite=True) as h, mock.patch.object(rule_shadow, "log") as lg:
            h.series.return_value = (None, None, FALLEN, None)
            out = bs._run([row])
        self.assertEqual(bs._syms(out), ["AAA"])                # the shadow changes nothing
        (entries,), _ = lg.call_args
        self.assertEqual(len(entries), 1)
        self.assertEqual((entries[0]["source"], entries[0]["symbol"], entries[0]["rr_waived"]), ("game", "AAA", True))
        self.assertTrue(entries[0]["would_hold_back"])

    def test_no_waiver_is_recorded_as_such(self):
        with bs._Harness() as h, mock.patch.object(rule_shadow, "log") as lg:
            h.series.return_value = (None, None, FALLEN, None)
            bs._run([bs._row(stop=95.0, target=110.0)])          # R:R 2.0: no waiver needed
        (entries,), _ = lg.call_args
        self.assertEqual((entries[0]["rr_waived"], entries[0]["would_hold_back"]), (False, False))


if __name__ == "__main__":
    unittest.main()
