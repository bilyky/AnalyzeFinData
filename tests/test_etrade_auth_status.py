"""
Hermetic, offline tests for the generic E*TRADE auth-detection primitive `auth_status`.

`auth_status(env, *, probe)` is the ONE read-only classifier reused by every interface
(web badge, /api/etrade/status, /api/health embed, `server.py etrade-status`). These tests
drive the REAL function and mock only the I/O seams — `_probe_token_auth` (broker GET),
`renew_tokens` (OAuth renew HTTP), and the browser choke points — so they run with no network
and never touch the production auth files (every path is redirected to a per-test temp dir by
the shared `_EtradeScenarioBase`).

The invariants under test mirror the ban-safety contract:
  * probe=False is pure-local: NO network, NO mutation (neither renew nor probe fires).
  * the local -> refresh -> probe ORDER holds (renew before probe, on the renewed token).
  * a previous-day (overnight/midnight-ET) token is ruled dead LOCALLY with no wasted probe.
  * a transient probe blip (None) never downgrades an otherwise-good same-day token.
  * `auth_status` NEVER opens a browser in ANY mode.
"""
import json
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import aether.etrade as etrade
from tests.test_etrade_auth_scenarios import _EtradeScenarioBase, _yesterday_et


class _AuthStatusBase(_EtradeScenarioBase):
    """Adds trust-marker + breaker seeding on top of the shared temp-dir isolation."""

    def _seed_trust(self, state):
        # 'unseeded' is the default (no marker file), so only write for trusted/sms_required.
        if state in ("trusted", "sms_required"):
            etrade._set_profile_trust("production", state)

    def _arm_breaker(self, minutes=30.0):
        etrade._save_reauth_state(
            {"consecutive_failures": 3, "last_attempt": time.time(),
             "cooldown_until": time.time() + minutes * 60},
            "production",
        )

    def _mock_io(self, *, probe_ret, renew_ret):
        """Patch renew_tokens + _probe_token_auth as MagicMocks attached to one manager so
        call ORDER across the two can be asserted. Returns (manager, m_renew, m_probe)."""
        manager = mock.Mock()
        p_renew = mock.patch.object(etrade, "renew_tokens", return_value=renew_ret)
        p_probe = mock.patch.object(etrade, "_probe_token_auth", return_value=probe_ret)
        m_renew = p_renew.start(); self.addCleanup(p_renew.stop)
        m_probe = p_probe.start(); self.addCleanup(p_probe.stop)
        manager.attach_mock(m_renew, "renew")
        manager.attach_mock(m_probe, "probe")
        return manager, m_renew, m_probe


class TestAuthStatusLocal(_AuthStatusBase):
    """probe=False and the locally-decidable states — no network expected."""

    def test_same_day_probe_false_is_live_no_network(self):
        self._write_token(etrade._et_today(), age_min=1.0)
        self._seed_trust("trusted")
        _, m_renew, m_probe = self._mock_io(probe_ret=True, renew_ret=None)
        r = etrade.auth_status("production", probe=False)
        self.assertEqual(r["state"], etrade.AuthReason.LIVE)
        self.assertEqual(r["probe"], "skipped")
        self.assertTrue(r["token"]["is_today"])
        # Ban-safety: probe=False touches NOTHING on the wire and mutates NOTHING.
        m_renew.assert_not_called()
        m_probe.assert_not_called()

    def test_previous_day_trusted_is_expired_can_auto_no_probe(self):
        # Overnight/midnight-ET hard expiry: ruled dead locally, renew can't bridge it,
        # a probe would only confirm rejected -> NEITHER is attempted.
        self._write_token(_yesterday_et(), age_min=90.0)
        self._seed_trust("trusted")
        _, m_renew, m_probe = self._mock_io(probe_ret=True, renew_ret=None)
        r = etrade.auth_status("production", probe=True)
        self.assertEqual(r["state"], etrade.AuthReason.EXPIRED)
        self.assertTrue(r["can_auto_reauth"])
        self.assertFalse(r["needs_manual_auth"])
        self.assertEqual(r["probe"], "skipped")
        m_renew.assert_not_called()
        m_probe.assert_not_called()

    def test_previous_day_sms_required_needs_manual(self):
        self._write_token(_yesterday_et(), age_min=90.0)
        self._seed_trust("sms_required")
        _, m_renew, m_probe = self._mock_io(probe_ret=True, renew_ret=None)
        r = etrade.auth_status("production", probe=True)
        self.assertEqual(r["state"], etrade.AuthReason.SMS_REQUIRED)
        self.assertTrue(r["needs_manual_auth"])
        self.assertFalse(r["can_auto_reauth"])
        m_renew.assert_not_called()
        m_probe.assert_not_called()

    def test_missing_token_unseeded_needs_manual(self):
        # No token file at all + no trust marker (unseeded default).
        _, m_renew, m_probe = self._mock_io(probe_ret=True, renew_ret=None)
        r = etrade.auth_status("production", probe=True)
        self.assertEqual(r["state"], etrade.AuthReason.UNSEEDED)
        self.assertTrue(r["needs_manual_auth"])
        self.assertFalse(r["token"]["present"])
        m_renew.assert_not_called()
        m_probe.assert_not_called()

    def test_missing_token_trusted_can_auto(self):
        self._seed_trust("trusted")
        _, m_renew, m_probe = self._mock_io(probe_ret=True, renew_ret=None)
        r = etrade.auth_status("production", probe=True)
        self.assertEqual(r["state"], etrade.AuthReason.MISSING)
        self.assertTrue(r["can_auto_reauth"])
        self.assertFalse(r["needs_manual_auth"])
        m_renew.assert_not_called()
        m_probe.assert_not_called()

    def test_breaker_cooling_reports_breaker(self):
        # Dead token (previous-day) + trusted, but the breaker is cooling -> BREAKER wins.
        self._write_token(_yesterday_et(), age_min=90.0)
        self._seed_trust("trusted")
        self._arm_breaker(30.0)
        r = etrade.auth_status("production", probe=False)
        self.assertEqual(r["state"], etrade.AuthReason.BREAKER)
        self.assertFalse(r["can_auto_reauth"])
        self.assertGreater(r["breaker"]["cooldown_remaining_min"], 0)

    def test_breaker_sub_3s_tail_still_reads_cooling(self):
        # Regression guard for the raw-seconds `cooling` fix: a ~2 s remaining cooldown rounds to
        # 0.0 display-minutes (breaker["cooldown_remaining_min"]), but auth_status must still read
        # the breaker as cooling off the RAW seconds — matching scheduled_reauth — so it reports
        # BREAKER and does NOT falsely claim can_auto_reauth on the last ~3 s of a cooldown.
        # (Deriving `cooling` from the rounded display value regresses this to EXPIRED/can_auto.)
        self._write_token(_yesterday_et(), age_min=90.0)
        self._seed_trust("trusted")
        self._arm_breaker(2.0 / 60.0)  # ~2 seconds remaining -> rounds to 0.0 min
        self.assertEqual(round(etrade._reauth_cooldown_remaining("production") / 60, 1), 0.0)
        r = etrade.auth_status("production", probe=False)
        self.assertEqual(r["state"], etrade.AuthReason.BREAKER)
        self.assertFalse(r["can_auto_reauth"])


class TestAuthStatusProbe(_AuthStatusBase):
    """probe=True on a SAME-DAY token — the refresh -> probe ladder."""

    def _renewed_token(self):
        return {"oauth_token": "fresh", "oauth_token_secret": "s2",
                "env": "production", "issued_date_et": etrade._et_today(),
                "saved_at": time.time()}

    def test_renew_then_probe_authorized_reason_renewed(self):
        self._write_token(etrade._et_today(), age_min=90.0)  # aged past the 55-min guard
        self._seed_trust("trusted")
        manager, m_renew, m_probe = self._mock_io(probe_ret=True, renew_ret=self._renewed_token())
        r = etrade.auth_status("production", probe=True)
        self.assertEqual(r["state"], etrade.AuthReason.LIVE)
        self.assertEqual(r["reason"], etrade.AuthReason.RENEWED)
        self.assertEqual(r["probe"], "authorized")
        # ORDER: renew must run BEFORE the probe...
        order = [c[0] for c in manager.mock_calls]
        self.assertLess(order.index("renew"), order.index("probe"))
        # ...and the probe must be against the RENEWED token, not the stale one on disk.
        probed = m_probe.call_args.args[0]
        self.assertEqual(probed["oauth_token"], "fresh")

    def test_probe_no_renew_reason_live(self):
        # renew is a no-op (returns None -> falls back to the same token): authorized but
        # NOT renewed, so the reason is plain 'live', not 'renewed'.
        self._write_token(etrade._et_today(), age_min=1.0)
        self._seed_trust("trusted")
        _, m_renew, m_probe = self._mock_io(probe_ret=True, renew_ret=None)
        r = etrade.auth_status("production", probe=True)
        self.assertEqual(r["state"], etrade.AuthReason.LIVE)
        self.assertEqual(r["reason"], etrade.AuthReason.LIVE)
        self.assertEqual(r["probe"], "authorized")
        m_renew.assert_called_once()
        m_probe.assert_called_once()

    def test_guard_reuse_truthy_but_unrenewed_reason_live(self):
        # renew_tokens returns the SAME token on a <55-min guard-reuse: truthy, but saved_at is
        # unchanged, so it is NOT a real renew. Reason must be LIVE, not RENEWED. Guards the
        # saved_at heuristic against being simplified to bool(renewed) (which would mislabel a
        # reuse as RENEWED) — the one case the two other probe tests (None / fresh-token) miss.
        self._write_token(etrade._et_today(), age_min=1.0)
        self._seed_trust("trusted")
        reused = etrade._load_tokens_any_date("production")  # same saved_at renew_tokens returns
        _, m_renew, m_probe = self._mock_io(probe_ret=True, renew_ret=reused)
        r = etrade.auth_status("production", probe=True)
        self.assertEqual(r["state"], etrade.AuthReason.LIVE)
        self.assertEqual(r["reason"], etrade.AuthReason.LIVE)   # NOT renewed
        self.assertEqual(r["probe"], "authorized")

    def test_probe_rejected_is_expired(self):
        self._write_token(etrade._et_today(), age_min=90.0)
        self._seed_trust("trusted")
        _, _, m_probe = self._mock_io(probe_ret=False, renew_ret=None)
        r = etrade.auth_status("production", probe=True)
        self.assertEqual(r["state"], etrade.AuthReason.EXPIRED)
        self.assertEqual(r["probe"], "rejected")
        self.assertTrue(r["can_auto_reauth"])  # trusted + breaker clear
        m_probe.assert_called_once()

    def test_probe_indeterminate_stays_live(self):
        # A transient blip (None) must NEVER downgrade a same-day token.
        self._write_token(etrade._et_today(), age_min=90.0)
        self._seed_trust("trusted")
        _, _, m_probe = self._mock_io(probe_ret=None, renew_ret=None)
        r = etrade.auth_status("production", probe=True)
        self.assertEqual(r["state"], etrade.AuthReason.LIVE)
        self.assertEqual(r["reason"], etrade.AuthReason.INDETERMINATE)
        self.assertEqual(r["probe"], "indeterminate")


class TestAuthStatusBanSafety(_AuthStatusBase):
    """The one non-negotiable: no interface, no mode, ever opens a browser."""

    def test_never_opens_browser_in_any_mode(self):
        self._write_token(etrade._et_today(), age_min=90.0)
        self._seed_trust("trusted")
        m_pw, m_lh = self._no_browser_guard()
        self._mock_io(probe_ret=True, renew_ret=None)
        for probe in (False, True):
            etrade.auth_status("production", probe=probe)
        m_pw.assert_not_called()
        m_lh.assert_not_called()

    def test_result_is_json_serializable(self):
        self._write_token(etrade._et_today(), age_min=1.0)
        self._seed_trust("trusted")
        r = etrade.auth_status("production", probe=False)
        # Every field must survive an HTTP/JSON round-trip (it ships in /api/health).
        json.dumps(r)
        for k in ("env", "state", "reason", "needs_manual_auth", "can_auto_reauth",
                  "token", "trust", "breaker", "probe", "checked_at_et", "summary"):
            self.assertIn(k, r)


if __name__ == "__main__":
    unittest.main()
