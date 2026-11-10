"""position_watch.py and invest_plan.py print text with non-cp1252 characters (pattern tags
such as 'RBR↑(gap,watch)', free-text plan labels). On 2026-11-06 a cp1252 console crashed the
position-watch report with UnicodeEncodeError after the journal entry was written. Each entry
point is run in a child process whose stdout is cp1252, as on a legacy Windows console."""
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MON = os.path.join(ROOT, "scripts", "monitoring")
TAG = "RBR↑(gap,watch)"

POSITION_WATCH = f"""
import sys
sys.path.insert(0, {MON!r})
from unittest import mock
import position_watch as pw
with mock.patch.object(pw, "run", return_value={{}}), \\
        mock.patch.object(pw, "load_config", return_value=dict(pw.DEFAULTS)), \\
        mock.patch.object(pw, "print_report", lambda rep: pw._out("  RBR 0.0 [{TAG}]")):
    sys.exit(pw.main(["--symbol", "ZZZ", "--no-journal"]))
"""

INVEST_PLAN = f"""
import sys
sys.path.insert(0, {MON!r})
from unittest import mock
import invest_plan as ip
plan = {{"name": "{TAG}", "steps": [{{"date": "2030-03-13", "amount": 100, "buy": "SPY {TAG}"}}],
         "rules": {{}}, "fills": []}}
days = ["2030-03-04", "2030-03-05"]
bars = {{"days": days, "idx": {{d: i for i, d in enumerate(days)}}, "c": [100.0, 100.0], "o": [100.0, 100.0],
         "h": [100.0, 100.0], "l": [100.0, 100.0], "v": [1.0, 1.0]}}
with mock.patch.object(ip, "load_plan", return_value=plan), mock.patch.object(ip.pw, "load_bars", return_value=bars):
    sys.exit(ip.main(["--plan", "unused.json"]))
"""


@unittest.skipUnless(sys.platform == "win32", "console_safe only acts on Windows")
class TestCp1252Console(unittest.TestCase):
    def _run(self, code):
        env = dict(os.environ, PYTHONIOENCODING="cp1252")
        return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, timeout=120)

    def test_position_watch_report_prints_an_arrow_tag(self):
        r = self._run(POSITION_WATCH)
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace")[-600:])
        self.assertIn(TAG.encode("utf-8"), r.stdout)

    def test_invest_plan_prints_a_non_cp1252_label(self):
        r = self._run(INVEST_PLAN)
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace")[-600:])
        self.assertIn(f"SPY {TAG}".encode("utf-8"), r.stdout)


if __name__ == "__main__":
    unittest.main()
