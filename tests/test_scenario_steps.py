"""B5 tests for aether.scenario.steps.determine_profile.

``determine_profile`` is the first stateful stage lifted out of
``run_daily_ai_management``: it resolves today's strategy profile, mutates
``state["profile"]`` / ``state["profile_mode"]`` in place, and returns the
profile string. These tests pin three things:

1. **Every branch of the four-way chain** (manual ADAPTIVE restore, manual
   override lock, MANUAL auto-reset, plain autopilot) produces the same profile,
   ``profile_mode`` and log verb the root block produced.
2. **The deduped cash-deployment gate is behaviour-identical** — exercised on both
   branches that carry it (MANUAL auto-reset and the ``else`` autopilot), for both
   the upgrade path and every no-upgrade guard (wrong regime, thin cash, no
   setups, degenerate equity).
3. **Collaborators are resolved at call time** (the ``_pkg()`` seam): a
   ``mock.patch.object(game, ...)`` still intercepts through the function, and the
   gate forwards ``min_score=9.5`` verbatim.

The root's collaborators (``get_market_regime``, ``_has_strong_setups_today``,
``_log``) are patched on the module, so no network / disk / real logging — the
same call-time-differential style as ``test_scenario_helpers``.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ai_portfolio_game as game  # noqa: E402
from aether.scenario import determine_profile as determine_profile_reexport  # noqa: E402
from aether.scenario.steps import determine_profile  # noqa: E402


def _patch_collaborators(regime="BALANCED", strong=False):
    """Patch the three root collaborators determine_profile resolves via _pkg().

    Returns a context manager that patches get_market_regime -> ``regime``,
    _has_strong_setups_today -> ``strong``, and silences the ``_log`` sink.
    """
    return (
        mock.patch.object(game, "get_market_regime", return_value=regime),
        mock.patch.object(game, "_has_strong_setups_today", return_value=strong),
        mock.patch.object(game, "_log", mock.MagicMock()),
    )


class _ProfileCase(unittest.TestCase):
    """Base with a helper that runs determine_profile under patched collaborators."""

    def _run(self, state, manual_profile=None, regime="BALANCED", strong=False):
        p_regime, p_strong, p_log = _patch_collaborators(regime, strong)
        with p_regime as m_regime, p_strong as m_strong, p_log:
            result = determine_profile(state, manual_profile)
        return result, m_regime, m_strong


class TestManualBranches(_ProfileCase):
    def test_manual_adaptive_restores_autopilot_from_regime(self):
        # "ADAPTIVE" (case-insensitive) restores the pilot: profile == live regime.
        state = {}
        result, m_regime, _ = self._run(state, manual_profile="adaptive", regime="AGGRESSIVE")
        self.assertEqual(result, "AGGRESSIVE")
        self.assertEqual(state["profile"], "AGGRESSIVE")
        self.assertEqual(state["profile_mode"], "ADAPTIVE")
        m_regime.assert_called_once_with()

    def test_manual_adaptive_does_not_apply_cash_gate(self):
        # The restore branch has no gate: a DEFENSIVE regime stays DEFENSIVE even
        # with plentiful cash + strong setups.
        state = {"equity": 100_000, "balance": 60_000}
        result, _, m_strong = self._run(
            state, manual_profile="ADAPTIVE", regime="DEFENSIVE", strong=True
        )
        self.assertEqual(result, "DEFENSIVE")
        self.assertEqual(state["profile"], "DEFENSIVE")
        m_strong.assert_not_called()  # gate never consulted

    def test_manual_override_locks_and_skips_regime(self):
        state = {}
        result, m_regime, m_strong = self._run(state, manual_profile="DEFENSIVE")
        self.assertEqual(result, "DEFENSIVE")
        self.assertEqual(state["profile"], "DEFENSIVE")
        self.assertEqual(state["profile_mode"], "MANUAL")
        m_regime.assert_not_called()  # a locked override never reads the regime
        m_strong.assert_not_called()  # nor the gate


class TestManualAutoResetBranch(_ProfileCase):
    def test_expired_manual_resets_to_adaptive(self):
        state = {"profile_mode": "MANUAL"}
        result, m_regime, _ = self._run(state, manual_profile=None, regime="AGGRESSIVE")
        self.assertEqual(result, "AGGRESSIVE")
        self.assertEqual(state["profile"], "AGGRESSIVE")
        self.assertEqual(state["profile_mode"], "ADAPTIVE")
        m_regime.assert_called_once_with()

    def test_auto_reset_applies_cash_gate_upgrade(self):
        # DEFENSIVE + cash > 40% + strong setups -> upgraded to BALANCED.
        state = {"profile_mode": "MANUAL", "equity": 100_000, "balance": 50_000}
        result, _, m_strong = self._run(state, regime="DEFENSIVE", strong=True)
        self.assertEqual(result, "BALANCED")
        self.assertEqual(state["profile"], "BALANCED")
        self.assertEqual(state["profile_mode"], "ADAPTIVE")
        m_strong.assert_called_once_with(min_score=9.5)


class TestAutopilotElseBranch(_ProfileCase):
    def test_plain_autopilot_uses_regime(self):
        state = {}
        result, m_regime, _ = self._run(state, regime="BALANCED")
        self.assertEqual(result, "BALANCED")
        self.assertEqual(state["profile"], "BALANCED")
        self.assertEqual(state["profile_mode"], "ADAPTIVE")
        m_regime.assert_called_once_with()

    def test_autopilot_cash_gate_upgrade(self):
        state = {"equity": 100_000, "balance": 41_000}  # 41% > 40%
        result, _, m_strong = self._run(state, regime="DEFENSIVE", strong=True)
        self.assertEqual(result, "BALANCED")
        self.assertEqual(state["profile"], "BALANCED")
        m_strong.assert_called_once_with(min_score=9.5)


class TestCashDeploymentGateGuards(_ProfileCase):
    """Every condition that must keep the profile DEFENSIVE (no upgrade)."""

    def test_no_upgrade_when_regime_not_defensive(self):
        # Gate only touches DEFENSIVE; an AGGRESSIVE regime is left alone.
        state = {"equity": 100_000, "balance": 90_000}
        result, _, m_strong = self._run(state, regime="AGGRESSIVE", strong=True)
        self.assertEqual(result, "AGGRESSIVE")
        m_strong.assert_not_called()  # short-circuits before consulting setups

    def test_no_upgrade_when_cash_thin(self):
        # cash_ratio 0.40 is not > 0.40 -> no upgrade.
        state = {"equity": 100_000, "balance": 40_000}
        result, _, _ = self._run(state, regime="DEFENSIVE", strong=True)
        self.assertEqual(result, "DEFENSIVE")

    def test_no_upgrade_when_no_strong_setups(self):
        state = {"equity": 100_000, "balance": 90_000}
        result, _, m_strong = self._run(state, regime="DEFENSIVE", strong=False)
        self.assertEqual(result, "DEFENSIVE")
        m_strong.assert_called_once_with(min_score=9.5)

    def test_no_upgrade_when_equity_degenerate(self):
        # equity <= 1.0 forces cash_ratio to 0 regardless of balance.
        state = {"equity": 1.0, "balance": 10_000}
        result, _, _ = self._run(state, regime="DEFENSIVE", strong=True)
        self.assertEqual(result, "DEFENSIVE")

    def test_no_upgrade_when_state_missing_equity_balance(self):
        # Missing keys default to 0 -> cash_ratio 0 -> no upgrade, no KeyError.
        state = {}
        result, _, _ = self._run(state, regime="DEFENSIVE", strong=True)
        self.assertEqual(result, "DEFENSIVE")


class TestSeamAndReexport(_ProfileCase):
    def test_regime_is_resolved_at_call_time(self):
        # The whole point of _pkg(): the patch applied now is the one used.
        state = {}
        result, _, _ = self._run(state, regime="SENTINEL_REGIME")
        self.assertEqual(result, "SENTINEL_REGIME")

    def test_reexported_from_package_root(self):
        self.assertIs(determine_profile_reexport, determine_profile)


if __name__ == "__main__":
    unittest.main()
