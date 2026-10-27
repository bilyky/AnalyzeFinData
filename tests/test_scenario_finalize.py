"""B6 tests for aether.scenario.steps.finalize_run.

``finalize_run`` is the ``finally:`` block of ``run_daily_ai_management``. These
tests pin what it carries over from the root, unchanged:

1. **Workbook** — closed when there is one; a failed close is ignored and the
   save still happens; ``None`` is skipped.
2. **Loaded state** — ``save_game`` then ``update_excel_log`` with the run's
   transactions, then the portfolio value is logged.
3. **No state** — nothing is saved or written, and the abort is logged.

Every collaborator is patched on the live module — no disk. Re-export and root
parity are pinned in ``test_scenario_steps`` and ``test_scenario_parity``.
"""
import os
import sys
import unittest
from contextlib import ExitStack
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ai_portfolio_game as game  # noqa: E402
from aether.scenario.steps import finalize_run  # noqa: E402


class _Harness:
    """Patch every collaborator finalize_run resolves via _pkg()."""

    def __init__(self):
        self.calls = mock.MagicMock()  # one parent, so call order is visible

    def __enter__(self):
        self._stack = ExitStack()
        for name in ("save_game", "update_excel_log", "_log"):
            self._stack.enter_context(mock.patch.object(game, name, getattr(self.calls, name)))
        return self

    def __exit__(self, *exc):
        return self._stack.__exit__(*exc)

    def names(self):
        return [c[0] for c in self.calls.mock_calls]


_STATE = {"equity": 12345.6, "balance": 6789.123}


class TestWorkbook(unittest.TestCase):
    def test_workbook_is_closed(self):
        wb = mock.MagicMock()
        with _Harness():
            finalize_run(dict(_STATE), wb, [])
        wb.close.assert_called_once_with()

    def test_failed_close_is_ignored_and_state_still_saved(self):
        wb = mock.MagicMock()
        wb.close.side_effect = OSError("locked")
        state = dict(_STATE)
        with _Harness() as h:
            finalize_run(state, wb, [])
        h.calls.save_game.assert_called_once_with(state)

    def test_no_workbook_is_skipped(self):
        with _Harness() as h:
            finalize_run(dict(_STATE), None, [])
        h.calls.save_game.assert_called_once()


class TestState(unittest.TestCase):
    def test_loaded_state_is_saved_logged_and_reported_in_order(self):
        state, txs = dict(_STATE), [{"type": "BUY"}]
        with _Harness() as h:
            finalize_run(state, None, txs)
        self.assertEqual(h.names(), ["save_game", "update_excel_log", "_log.info"])
        h.calls.update_excel_log.assert_called_once_with(state, txs)
        self.assertIs(h.calls.update_excel_log.call_args.args[1], txs)
        self.assertEqual(h.calls._log.info.call_args.args[0],
                         "🤖 AI Portfolio Value: $12345.6 (Cash: $6789.12)")

    def test_no_state_saves_nothing_and_logs_the_abort(self):
        wb = mock.MagicMock()
        with _Harness() as h:
            finalize_run(None, wb, [])
        wb.close.assert_called_once_with()
        self.assertEqual(h.names(), ["_log.info"])
        self.assertIn("aborted before state initialization", h.calls._log.info.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
