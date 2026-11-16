"""The weekly R&D pattern discovery runs without the Gemini CLI.

The AETHER_RD_Scientist agent task failed with HTTP 403 every Saturday from at least 2026-09-19 to
2026-11-14, so no pattern-discovery report was written after the 08-18 replay. The new task runs the
computation in Python and the analysis through our own AI layer (aether.ai_client, config `ai`).
When the AI step can't run, it is skipped and the owner is told why; the computed report is kept.
"""
import datetime
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
import rd_pattern_discovery as rd
from aether import ai_step, scheduled_tasks

_REPORT = {"replay_date": "2026-10-22",
           "s10_analysis": {"missed": [{"symbol": "AAA"}], "false_positives": [{"symbol": "BBB"}], "universe_avg": 1.2},
           "l60_analysis": {"missed": [], "false_positives": [], "universe_avg": 4.0},
           "candidates_s10": [], "candidates_l60": [], "fp_guards_s10": [], "fp_guards_l60": []}


class AiStepTest(unittest.TestCase):
    def test_returns_the_answer(self):
        with mock.patch.object(ai_step.ai_client, "evaluate_with_provider", return_value=("findings", "groq")), \
             mock.patch.object(ai_step.notify, "send_email") as send:
            r = ai_step.run("pattern discovery", "sys", "user", tier="strong")
        self.assertEqual((r.text, r.provider, r.skipped), ("findings", "groq", None))
        send.assert_not_called()

    def test_a_failure_is_skipped_logged_and_emailed(self):
        with mock.patch.object(ai_step.ai_client, "evaluate_with_provider", side_effect=RuntimeError("HTTP 403 forbidden")), \
             mock.patch.object(ai_step.notify, "send_email") as send, \
             self.assertLogs("aether.ai_step", level="WARNING") as logs:
            r = ai_step.run("pattern discovery", "sys", "user", tier="strong")
        self.assertIsNone(r.text)
        self.assertIn("HTTP 403", r.skipped)
        self.assertTrue(any("skipped" in m and "HTTP 403" in m for m in logs.output))
        send.assert_called_once()
        self.assertIn("AI step skipped", send.call_args.args[0])
        self.assertIn("HTTP 403", send.call_args.args[1])

    def test_an_empty_answer_is_a_skip(self):
        with mock.patch.object(ai_step.ai_client, "evaluate_with_provider", return_value=("   ", "groq")), \
             mock.patch.object(ai_step.notify, "send_email"):
            r = ai_step.run("x", "sys", "user")
        self.assertIsNone(r.text)
        self.assertIn("empty", r.skipped)

    def test_no_email_when_asked(self):
        with mock.patch.object(ai_step.ai_client, "evaluate_with_provider", side_effect=RuntimeError("No primary AI provider configured.")), \
             mock.patch.object(ai_step.notify, "send_email") as send:
            r = ai_step.run("x", "sys", "user", notify_on_skip=False)
        self.assertIn("No primary AI provider", r.skipped)
        send.assert_not_called()


class _Run(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.out = Path(self._td.name)
        self._p = mock.patch.object(rd, "OUT_DIR", self.out)
        self._p.start()

    def tearDown(self):
        self._p.stop()
        self._td.cleanup()

    def _fake_compute(self, rc=0, write=True):
        def run(cmd, **kw):
            if write:
                (self.out / f"pattern_discovery_{cmd[cmd.index('--date') + 1]}.json").write_text(json.dumps(_REPORT), encoding="utf-8")
            return subprocess.CompletedProcess(cmd, rc, stdout="", stderr="boom" if rc else "")
        return run

    def _main(self, argv, compute, ai):
        with mock.patch.object(rd.subprocess, "run", side_effect=compute) as sp, \
             mock.patch.object(rd.ai_step, "run", side_effect=ai) as step, \
             mock.patch.object(rd.notify, "send_email") as send, \
             mock.patch.object(sys, "argv", ["rd_pattern_discovery.py", *argv]):
            rc = rd.main()
        return rc, sp, step, send


class RunnerTest(_Run):
    def test_replay_date_defaults_to_25_days_back(self):
        self.assertEqual(rd.replay_date(datetime.date(2026, 11, 16)), "2026-10-22")

    def test_the_prompt_comes_from_the_skill_file(self):
        system = rd.analysis_instructions()
        self.assertIn("Step 4", system)
        self.assertIn("Do not recommend weight changes", system)
        self.assertNotIn("Step 2 — Run the computation script", system)

    def test_ai_findings_are_saved_and_emailed(self):
        ok = lambda name, system, user, **k: ai_step.StepResult("FINDINGS TEXT", "groq", None)
        rc, sp, step, send = self._main(["--date", "2026-10-22"], self._fake_compute(), ok)
        self.assertEqual(rc, 0)
        cmd = sp.call_args.args[0]
        self.assertIn("pattern_discovery.py", " ".join(cmd))
        self.assertEqual(cmd[cmd.index("--date") + 1], "2026-10-22")
        self.assertIn('"AAA"', step.call_args.args[2])                     # the report goes to the AI
        self.assertEqual((self.out / "pattern_discovery_2026-10-22_ai.md").read_text(encoding="utf-8").strip(), "FINDINGS TEXT")
        self.assertIn("2026-10-22", send.call_args.args[0])
        self.assertIn("FINDINGS TEXT", send.call_args.args[1])

    def test_a_skipped_ai_step_keeps_the_report_and_says_so(self):
        skip = lambda name, system, user, **k: ai_step.StepResult(None, None, "HTTP 403 forbidden")
        rc, _, _, send = self._main(["--date", "2026-10-22"], self._fake_compute(), skip)
        self.assertEqual(rc, 0)
        self.assertTrue((self.out / "pattern_discovery_2026-10-22.json").exists())
        self.assertFalse((self.out / "pattern_discovery_2026-10-22_ai.md").exists())
        subject, body = send.call_args.args[0], send.call_args.args[1]
        self.assertIn("AI analysis skipped", subject)
        self.assertIn("HTTP 403", body)
        self.assertIn("S10: 1 missed, 1 false positives", body)          # the computed counts still go out

    def test_the_skip_email_is_not_sent_twice(self):
        """ai_step is called with notify_on_skip=False: the runner's own email carries the reason."""
        skip = lambda name, system, user, **k: ai_step.StepResult(None, None, "down")
        _, _, step, send = self._main(["--date", "2026-10-22"], self._fake_compute(), skip)
        self.assertIs(step.call_args.kwargs.get("notify_on_skip"), False)
        self.assertEqual(send.call_count, 1)

    def test_a_failed_computation_is_reported_and_skips_the_ai(self):
        rc, _, step, send = self._main(["--date", "2026-10-22"], self._fake_compute(rc=1, write=False), None)
        self.assertEqual(rc, 1)
        step.assert_not_called()
        self.assertIn("failed", send.call_args.args[0])
        self.assertIn("boom", send.call_args.args[1])


class ManifestTest(unittest.TestCase):
    def test_weekly_python_task_replaces_the_agent(self):
        tasks = {t["name"]: t for t in scheduled_tasks.tasks()}
        t = tasks["AETHER_PatternDiscovery"]
        self.assertEqual(t["script"], "scripts/backtesting/rd_pattern_discovery.py")
        self.assertEqual((t["schedule"]["type"], t["schedule"].get("day")), ("weekly", "Saturday"))
        self.assertTrue((Path(__file__).resolve().parent.parent / t["script"]).exists())
        self.assertNotIn("AETHER_RD_Scientist", tasks)                      # still retired


if __name__ == "__main__":
    unittest.main()
