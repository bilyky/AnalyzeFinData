"""B6 tests for aether.scenario.steps.open_research_workbook.

``open_research_workbook`` is the workbook gate of ``run_daily_ai_management``,
between the profile/rules choice and :func:`assemble_symbol_universe`. These
tests pin what it carries over from the root, unchanged:

1. **No workbook** — logged, nothing opened, ``None`` (the run stops).
2. **Stale workbook** (#176) — error logged, alert email with the reason and
   today's date, nothing opened, ``None``; a failed email is logged, not raised.
3. **Fresh workbook** — opened read-only with cached values; returns
   ``(wb, wb["Research"])``.

Every collaborator is patched on the live module — no disk or email. Re-export
and root parity are pinned in ``test_scenario_steps`` and
``test_scenario_parity``.
"""
import os
import sys
import unittest
from contextlib import ExitStack
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ai_portfolio_game as game  # noqa: E402
from aether.scenario.steps import open_research_workbook  # noqa: E402


_TODAY = "2026-10-30"


class _Harness:
    """Patch every collaborator open_research_workbook resolves via _pkg()."""

    def __init__(self, exists=True, stale=None, email_error=None):
        self.path = mock.MagicMock()
        self.path.exists.return_value = exists
        self.log = mock.MagicMock()
        self.staleness = mock.MagicMock(return_value=stale)
        self.email = mock.MagicMock(side_effect=email_error)
        self.ws = object()
        self.wb = mock.MagicMock()
        self.wb.__getitem__.side_effect = lambda name: self.ws if name == "Research" else None
        self.load = mock.MagicMock(return_value=self.wb)

    def __enter__(self):
        self._stack = ExitStack()
        for ctx in (
            mock.patch.object(game, "XLSX_FILE", self.path),
            mock.patch.object(game, "_log", self.log),
            mock.patch.object(game, "workbook_staleness", self.staleness),
            mock.patch.object(game.notify, "send_email", self.email),
            mock.patch.object(game.openpyxl, "load_workbook", self.load),
        ):
            self._stack.enter_context(ctx)
        return self

    def __exit__(self, *exc):
        return self._stack.__exit__(*exc)


class TestMissingWorkbook(unittest.TestCase):
    def test_missing_file_defers_the_run(self):
        with _Harness(exists=False) as h:
            self.assertIsNone(open_research_workbook(_TODAY))
        h.log.info.assert_called_once_with("Workbook not found. AI Management deferred.")
        h.staleness.assert_not_called()
        h.load.assert_not_called()


class TestStaleWorkbook(unittest.TestCase):
    def test_stale_workbook_is_refused_and_alerted(self):
        reason = "workbook was last written before the previous close"
        with _Harness(stale=reason) as h:
            self.assertIsNone(open_research_workbook(_TODAY))
        h.staleness.assert_called_once_with(h.path)
        self.assertIn(f"Refusing to trade on a stale workbook: {reason}.", h.log.error.call_args.args[0])
        subject, body = h.email.call_args.args
        self.assertEqual(subject, f"ALERT: AI trades skipped - stale workbook ({_TODAY})")
        self.assertIn(f"did not act because the {reason}.", body)
        self.assertIn("python ai_portfolio_game.py --run --force", body)
        h.load.assert_not_called()

    def test_failed_alert_is_logged_not_raised(self):
        with _Harness(stale="old", email_error=OSError("smtp down")) as h:
            self.assertIsNone(open_research_workbook(_TODAY))
        self.assertEqual(h.log.error.call_count, 2)
        self.assertIn("Could not send the stale-workbook alert: smtp down", h.log.error.call_args.args[0])
        h.load.assert_not_called()


class TestFreshWorkbook(unittest.TestCase):
    def test_fresh_workbook_opens_the_research_sheet(self):
        for fresh in (None, ""):
            with self.subTest(staleness=fresh), _Harness(stale=fresh) as h:
                wb, ws = open_research_workbook(_TODAY)
                h.load.assert_called_once_with(h.path, read_only=True, data_only=True)
                self.assertIs(wb, h.wb)
                self.assertIs(ws, h.ws)
                h.email.assert_not_called()
                h.log.error.assert_not_called()


if __name__ == "__main__":
    unittest.main()
