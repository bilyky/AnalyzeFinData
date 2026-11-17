"""A skill's read-only checks run in Python and its report is written by our AI layer, not the Gemini CLI.

AETHER_PostMarketReporter ran the `status` skill through the Gemini CLI and failed with HTTP 403 on
PROD. skill_report.py runs the skill's `python -c` checks itself, then asks aether.ai_step (config
`ai`) to write the report in the skill's format. If the AI step is skipped, the raw check outputs
are emailed with the reason.
"""
import datetime
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "utils"))
import skill_report as sr
from aether import ai_step, scheduled_tasks

_REPO = Path(__file__).resolve().parent.parent


class ExtractTest(unittest.TestCase):
    def test_the_status_skill_has_five_checks(self):
        checks = sr.extract_checks((_REPO / ".claude" / "commands" / "status.md").read_text(encoding="utf-8"))
        self.assertEqual([c.label for c in checks], ["Step 1 — Data freshness", "Step 2 — Portfolio financials",
                                                    "Step 3 — Scheduled task health", "Step 4 — Recent errors",
                                                    "Step 5 — Market regime"])
        for c in checks:
            with self.subTest(check=c.label):
                compile(c.code, c.label, "exec")                  # bash escaping undone: valid Python
        self.assertIn('pf["equity"]', checks[1].code)
        self.assertIn("f'Equity:  ${", checks[1].code)
        self.assertIn('if not t.get("retired")', checks[2].code)   # retired tasks aren't reported as missing

    def test_only_python_dash_c_blocks_run(self):
        """A block that runs a script (e.g. daily-run's `python autonomous_pipeline.py`) is never run."""
        text = "## A\n```bash\npython autonomous_pipeline.py\n```\n## B\n```bash\npython -c \"print(1)\"\n```\n"
        self.assertEqual([c.code for c in sr.extract_checks(text)], ["print(1)"])

    def test_the_status_checks_only_read(self):
        """They run unattended at 14:00 on PROD. A new block that writes or calls out is refused at
        run time; this pins today's blocks so such a change shows up here first."""
        checks = sr.extract_checks((_REPO / ".claude" / "commands" / "status.md").read_text(encoding="utf-8"))
        self.assertEqual({c.label: sr.not_read_only(c.code) for c in checks}, {c.label: "" for c in checks})

    def test_a_check_that_is_more_than_a_read_is_named(self):
        for code, hit in [("from aether import etrade; etrade.get_quotes(['X'])", "etrade"),
                          ("from ai_portfolio_game import get_live_prices", "get_live_prices"),
                          ("import notify; notify.send_email('s', 'b')", "notify"),
                          ("from aether.notify import x; x.send_email('s')", "notify"),
                          ("send_email('s', 'b')", "send_email"),
                          ("import subprocess; subprocess.run(['x'])", "subprocess"),
                          ("import requests; requests.get('http://x')", "requests"),
                          ("open('Data/x.json', 'w').write('{}')", "open('Data/x.json', 'w'"),
                          ("open(p, mode='a+')", "open(p, mode='a+'"),
                          ("import json; json.dump({}, f)", "json.dump("),
                          ("import os; os.remove('x')", "os.remove"),
                          ("wb.save('x.xlsx')", ".save(")]:
            with self.subTest(code=code):
                self.assertEqual(sr.not_read_only(code), hit)
        for code in ("print(open('x.json').read())", "open('wax.json', 'r', encoding='utf-8')", "open(p, 'rb')",
                     "import data_api"):
            with self.subTest(code=code):
                self.assertEqual(sr.not_read_only(code), "")

    def test_the_report_section_is_the_ai_instruction(self):
        self.assertIn("Flag any stale data", sr.report_instructions(
            (_REPO / ".claude" / "commands" / "status.md").read_text(encoding="utf-8")))


class RunnerTest(unittest.TestCase):
    def _main(self, argv, ai, rc=0, today=datetime.date(2026, 11, 16)):
        def run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, rc, stdout=f"out of {cmd[2][:12]!r}", stderr="err!" if rc else "")
        with mock.patch.object(sr.subprocess, "run", side_effect=run) as sp, \
             mock.patch.object(sr.ai_step, "run", side_effect=ai) as step, \
             mock.patch.object(sr.notify, "send_email") as send, \
             mock.patch.object(sr, "_today", return_value=today), \
             mock.patch.object(sys, "argv", ["skill_report.py", *argv]):
            code = sr.main()
        return code, sp, step, send

    def test_report_written_by_the_ai_is_emailed(self):
        ok = lambda name, system, user, **k: ai_step.StepResult("| Check | Result |", "groq", None)
        code, sp, step, send = self._main(["--skill", "status", "--title", "Portfolio status"], ok)
        self.assertEqual(code, 0)
        self.assertEqual(sp.call_count, 5)
        self.assertEqual(sp.call_args.args[0][:2], [sys.executable, "-c"])
        self.assertIn("Step 2", step.call_args.args[2])
        self.assertIs(step.call_args.kwargs.get("notify_on_skip"), False)
        self.assertIn("Portfolio status", send.call_args.args[0])
        self.assertIn("| Check | Result |", send.call_args.args[1])

    def test_a_skipped_ai_step_emails_the_raw_outputs_and_the_reason(self):
        skip = lambda name, system, user, **k: ai_step.StepResult(None, None, "HTTP 403 forbidden")
        code, _, _, send = self._main(["--skill", "status", "--title", "Portfolio status"], skip)
        self.assertEqual(code, 0)
        self.assertIn("AI report skipped", send.call_args.args[0])
        body = send.call_args.args[1]
        self.assertIn("HTTP 403", body)
        self.assertIn("Step 5 — Market regime", body)
        self.assertEqual(send.call_count, 1)

    def test_a_failing_check_is_reported_not_hidden(self):
        ok = lambda name, system, user, **k: ai_step.StepResult("report", "groq", None)
        _, _, step, _ = self._main(["--skill", "status", "--title", "t"], ok, rc=1)
        self.assertIn("FAILED (exit 1)", step.call_args.args[2])
        self.assertIn("err!", step.call_args.args[2])

    def test_a_writing_check_is_refused_and_not_run(self):
        skill = "# s\n## Safe\n```bash\npython -c \"print(1)\"\n```\n## Writes\n```bash\npython -c \"open('x','w')\"\n```\n"
        ok = lambda name, system, user, **k: ai_step.StepResult("r", "groq", None)
        with mock.patch.object(Path, "read_text", return_value=skill):
            _, sp, step, _ = self._main(["--skill", "status", "--title", "t"], ok)
        self.assertEqual(sp.call_count, 1)                     # only the safe block ran
        self.assertEqual(sp.call_args.args[0][2], "print(1)")
        self.assertIn("REFUSED, not run", step.call_args.args[2])

    def test_sessions_only_skips_weekends(self):
        code, sp, step, send = self._main(["--skill", "status", "--title", "t", "--sessions-only"], None,
                                          today=datetime.date(2026, 11, 14))
        self.assertEqual(code, 0)
        sp.assert_not_called()
        send.assert_not_called()


class ManifestTest(unittest.TestCase):
    def test_status_report_task_replaces_the_agent(self):
        tasks = {t["name"]: t for t in scheduled_tasks.tasks()}
        t = tasks["AETHER_StatusReport"]
        self.assertEqual(t["script"], "scripts/utils/skill_report.py")
        self.assertIn("--skill status", t["args"])
        self.assertIn("--sessions-only", t["args"])
        for retired in ("AETHER_DailyDriver", "AETHER_PostMarketSync", "AETHER_PostMarketReporter", "AETHER_RD_Scientist"):
            self.assertNotIn(retired, tasks)

    def test_no_active_task_runs_the_cli_agent(self):
        self.assertEqual([t["name"] for t in scheduled_tasks.tasks() if "prompt" in t], [])


if __name__ == "__main__":
    unittest.main()
