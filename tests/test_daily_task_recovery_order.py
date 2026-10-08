"""
daily_task.main() runs the OHLCV recovery pass LAST.

A full pass is long (its timeout covers the whole per-run fetch budget — see
rapidapi.pass_timeout_seconds), so running it before the report would push the evening
email back by the length of the pass. Pins:
  - the report email is sent BEFORE the recovery pass;
  - the pass still runs when the report path fails or returns early (no data);
  - --report-only never runs it;
  - it is invoked with the budgeted timeout, not run_command's 600 s default.
Everything external (subprocesses, email, sync, data load) is mocked.
"""
import datetime
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import daily_task
import rapidapi

_ROW = {"symbol": "AAA", "pgr": "Bu", "setup": 1, "s10": 5.0, "br": 2.0, "l60": 3.0}


class TestRecoveryRunsLast(unittest.TestCase):
    def setUp(self):
        self.calls = []
        lint_ok = mock.Mock(returncode=0, stdout="", stderr="")
        patches = [
            mock.patch.object(daily_task.os, "chdir"),
            mock.patch.object(daily_task.subprocess, "run", return_value=lint_ok),
            mock.patch.object(daily_task, "get_description", return_value=""),
            mock.patch.object(daily_task.watchdog, "sync_data_folder",
                              side_effect=lambda: self.calls.append("sync") or True),
            mock.patch.object(daily_task.notify, "send_email",
                              side_effect=lambda subject, *a, **k: self.calls.append(f"email:{subject}")),
            mock.patch.object(daily_task, "run_command",
                              side_effect=lambda cmd, timeout=600: self.calls.append(
                                  ("cmd", os.path.basename(cmd[1]), timeout))),
            mock.patch.object(daily_task.CFG, "has_placeholders", False, create=True),
            # The OceanView pack build is recorded, never run here: it would touch the broker
            # path and write Data/ files. Its own behaviour is pinned in test_oceanview_context.
            mock.patch.object(daily_task, "build_context_pack",
                              side_effect=lambda: self.calls.append("pack")),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _main(self, argv=(), data=(_ROW,)):
        get_data = (mock.patch.object(daily_task, "get_all_data", side_effect=data)
                    if isinstance(data, Exception)
                    else mock.patch.object(daily_task, "get_all_data", return_value=list(data)))
        with get_data, mock.patch.object(sys, "argv", ["daily_task.py", *argv]):
            daily_task.main()
        return self.calls

    def _recovery_index(self, calls):
        return next(i for i, c in enumerate(calls) if c[:2] == ("cmd", "rapidapi.py"))

    def test_report_email_goes_out_before_the_recovery_pass(self):
        calls = self._main()
        report = next(i for i, c in enumerate(calls)
                      if isinstance(c, str) and c.startswith("email:AETHER Daily Rotation"))
        self.assertLess(report, self._recovery_index(calls))
        self.assertLess(calls.index("sync"), self._recovery_index(calls))
        self.assertEqual(calls[-2:], ["pack", calls[self._recovery_index(calls)]])  # pack, then repair, last

    def test_recovery_uses_the_budgeted_timeout(self):
        calls = self._main()
        self.assertEqual(calls[self._recovery_index(calls)][2], rapidapi.pass_timeout_seconds())

    def test_recovery_still_runs_when_the_report_fails(self):
        calls = self._main(data=RuntimeError("workbook locked"))
        self.assertTrue(any(isinstance(c, str) and c.startswith("email:ALERT") for c in calls))
        self.assertEqual(calls[-2], "pack")
        self.assertEqual(calls[-1][:2], ("cmd", "rapidapi.py"))

    def test_recovery_still_runs_on_the_no_data_early_return(self):
        calls = self._main(data=())
        self.assertEqual(calls[-2], "pack")
        self.assertEqual(calls[-1][:2], ("cmd", "rapidapi.py"))

    def test_pack_runs_after_the_report_and_before_the_repair(self):
        # The pack's live broker read needs the same-day ET token (gone at 21:00 PT); the
        # repair pass can run for hours, so the pack is built after the report, BEFORE it.
        calls = self._main()
        report = next(i for i, c in enumerate(calls)
                      if isinstance(c, str) and c.startswith("email:AETHER Daily Rotation"))
        self.assertLess(report, calls.index("pack"))
        self.assertLess(calls.index("pack"), self._recovery_index(calls))

    def test_report_only_never_runs_recovery(self):
        calls = self._main(argv=["--report-only"])
        self.assertFalse(any(c[:2] == ("cmd", "rapidapi.py") for c in calls if isinstance(c, tuple)))
        self.assertNotIn("pack", calls)


class TestContextPackStep(unittest.TestCase):
    """daily_task.build_context_pack: ban-safe live build, weekend staleness, non-fatal."""

    def test_builds_live_with_the_day_appropriate_staleness(self):
        meta = {"health": "ok", "source": "live", "warnings": []}
        with mock.patch.object(daily_task.oceanview_context, "build_oceanview_context",
                               return_value={"meta": meta}) as b,              mock.patch.object(daily_task, "pack_max_stale_hours", return_value=24):
            daily_task.build_context_pack()
        b.assert_called_once_with(live=True, max_stale_hours=24)

    def test_weekend_allows_fridays_snapshot(self):
        self.assertEqual(daily_task.pack_max_stale_hours(datetime.date(2026, 10, 10)), 72)  # Saturday
        self.assertEqual(daily_task.pack_max_stale_hours(datetime.date(2026, 10, 11)), 72)  # Sunday
        self.assertEqual(daily_task.pack_max_stale_hours(datetime.date(2026, 10, 12)), 24)  # Monday

    def test_a_pack_failure_never_raises(self):
        with mock.patch.object(daily_task.oceanview_context, "build_oceanview_context",
                               side_effect=RuntimeError("broker down")):
            daily_task.build_context_pack()          # must not raise


if __name__ == "__main__":
    unittest.main()
