"""Hermetic, offline tests for the on-demand ("lazy") E*TRADE re-auth hardening.

These cover the changes layered on top of the existing automated door (scheduled_reauth) and the
lazy get_tokens() mint, all mocked at the true I/O seams (no E*TRADE, no browser, no SMTP):

  * config knobs — etrade_reauth_wait_timeout_sec (default 10) and etrade_reauth_alert_threshold
    (default 3), each overridable by config-file then env;
  * the loosened lazy-mint gate — get_tokens() reaches the headless mint when the legacy
    browser-state file is ABSENT but a TOTP secret is configured (the proven headless path),
    and the renewer is built with the SHORT config wait_timeout (a concurrent loser never blocks
    an inline data request on the rare overnight browser mint);
  * non-blocking single-flight on scheduled_reauth — a concurrent mint in flight makes the loser
    report reason="in_progress" (not a false "failed") and open no second browser; the lock is
    released on the happy path so a later call can win it again;
  * the 3-strike HARD BLOCK — once the consecutive-failure streak reaches the threshold the mint
    is suppressed regardless of the cooldown clock, the throttled alert fires once, and the
    read-only auth_status reports can_auto_reauth=False / needs_manual_auth=True / blocked=True.

The circuit-breaker cooldown mechanics live in test_etrade_reauth_circuit_breaker.py; the daily
door's trust/breaker gate lives in test_etrade_scheduled_reauth.py. This file is only the lazy
hardening + failure reporting.
"""
import contextlib
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tests  # noqa: F401  — globally locks hermeticity and redirects prod Data/ to temp
import config as config_module
from aether import etrade
from aether.token_renewer import single_flight


# ---------------------------------------------------------------------------
# 1. Config knobs — defaults, config-file, env override
# ---------------------------------------------------------------------------

def _make_cfg(raw: dict | None, env: dict | None = None):
    """Build a fresh _Config from a raw config-file dict + optional env overrides (mirrors
    test_config.py's helper). Restores _load_file and os.environ on the way out."""
    original_env = os.environ.copy()
    if env:
        os.environ.update(env)
    try:
        orig_load = config_module._load_file
        config_module._load_file = lambda: raw if raw is not None else {}
        return config_module._Config()
    finally:
        config_module._load_file = orig_load
        os.environ.clear()
        os.environ.update(original_env)


class TestLazyReauthConfigKnobs(unittest.TestCase):
    def test_defaults_when_absent(self):
        cfg = _make_cfg({})
        self.assertEqual(cfg.etrade_reauth_wait_timeout_sec, 10)
        self.assertEqual(cfg.etrade_reauth_alert_threshold, 3)

    def test_from_config_file(self):
        cfg = _make_cfg({"etrade": {"reauth_wait_timeout_sec": 25, "reauth_alert_threshold": 5}})
        self.assertEqual(cfg.etrade_reauth_wait_timeout_sec, 25)
        self.assertEqual(cfg.etrade_reauth_alert_threshold, 5)

    def test_env_overrides_file(self):
        cfg = _make_cfg(
            {"etrade": {"reauth_wait_timeout_sec": 25, "reauth_alert_threshold": 5}},
            env={"ETRADE_REAUTH_WAIT_TIMEOUT_SEC": "7", "ETRADE_REAUTH_ALERT_THRESHOLD": "2"},
        )
        self.assertEqual(cfg.etrade_reauth_wait_timeout_sec, 7)
        self.assertEqual(cfg.etrade_reauth_alert_threshold, 2)

    def test_empty_env_falls_through_to_default(self):
        # Empty-string env var is falsy → the `or` falls through to the file/default value.
        cfg = _make_cfg({}, env={"ETRADE_REAUTH_WAIT_TIMEOUT_SEC": ""})
        self.assertEqual(cfg.etrade_reauth_wait_timeout_sec, 10)


# ---------------------------------------------------------------------------
# 2. Loosened lazy-mint gate + short bounded wait (get_tokens)
# ---------------------------------------------------------------------------

class TestGetTokensLazyGate(unittest.TestCase):
    """The automated headless mint inside get_tokens() must fire when EITHER a saved browser-state
    file exists OR a TOTP secret is configured — and always with the short config wait_timeout."""

    def _drive(self, *, browser_state_exists, totp_secret, wait_timeout=8):
        """Run get_tokens() with every pre-mint ladder rung forced dead so control reaches the
        gate at line ~1261, and the mint renewer stubbed. Returns the _TokenRenewer mock class."""
        renewer_instance = mock.MagicMock()
        renewer_instance.ensure.return_value = None      # mint "fails" → get_tokens falls to None
        patches = [
            mock.patch.object(etrade, "_load_config", return_value=("ck", "cs", "u", "pw")),
            mock.patch.object(etrade, "_load_tokens", return_value=None),          # no today token
            mock.patch.object(etrade, "_load_tokens_any_date", return_value=None),  # no stale token
            mock.patch.object(etrade.os.path, "exists", return_value=browser_state_exists),
            mock.patch.object(etrade, "_reauth_cooldown_remaining", return_value=0.0),  # breaker open
            mock.patch.object(etrade.CFG, "etrade_totp_secret", totp_secret),
            mock.patch.object(etrade.CFG, "etrade_reauth_wait_timeout_sec", wait_timeout),
            mock.patch.object(etrade, "_TokenRenewer", return_value=renewer_instance),  # last → mint
        ]
        started = [p.start() for p in patches]
        self.addCleanup(lambda: [p.stop() for p in patches])
        m_renewer = started[-1]                           # the _TokenRenewer mock
        etrade.get_tokens("production", allow_browser=False)
        return m_renewer

    def test_totp_secret_opens_gate_when_browser_state_absent(self):
        m_renewer = self._drive(browser_state_exists=False, totp_secret="SECRET")
        m_renewer.assert_called_once()                   # gate opened via TOTP, not the state file
        self.assertEqual(m_renewer.call_args.kwargs["wait_timeout"], 8)   # SHORT config wait

    def test_browser_state_alone_still_opens_gate(self):
        m_renewer = self._drive(browser_state_exists=True, totp_secret="")
        m_renewer.assert_called_once()                   # legacy device-trust path unchanged

    def test_gate_closed_when_neither_present(self):
        m_renewer = self._drive(browser_state_exists=False, totp_secret="")
        m_renewer.assert_not_called()                    # no mint path available → no browser


# ---------------------------------------------------------------------------
# 3. Non-blocking single-flight on scheduled_reauth
# ---------------------------------------------------------------------------

class TestScheduledReauthSingleFlight(unittest.TestCase):
    def _patches(self, *, trust="trusted", cooldown=0.0):
        return [
            mock.patch.object(etrade, "keep_alive", return_value=None),
            mock.patch.object(etrade, "_profile_trust_state", return_value=trust),
            mock.patch.object(etrade, "_reauth_cooldown_remaining", return_value=cooldown),
            mock.patch.object(etrade, "_load_config", return_value=("ck", "cs", "u", "pw")),
            mock.patch.object(etrade, "_breaker_summary", return_value={}),
            mock.patch.object(etrade, "_set_profile_trust"),
            # Not blocked (streak 0) so the gate reaches the single-flight section.
            mock.patch.object(etrade, "_load_reauth_state",
                              return_value={"consecutive_failures": 0, "cooldown_until": 0.0,
                                            "last_attempt": 0.0}),
        ]

    def test_lock_held_reports_in_progress_no_browser(self):
        # A mint already in flight (single_flight yields False) → the loser must report in_progress
        # and open NO second browser.
        @contextlib.contextmanager
        def _loser(*_a, **_k):
            yield False
        ps = self._patches()
        for p in ps:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in ps])
        with mock.patch.object(etrade, "_single_flight", _loser), \
             mock.patch.object(etrade, "_login_headless") as m_lh:
            res = etrade.scheduled_reauth("production")
        self.assertEqual(res["reason"], "in_progress")
        self.assertFalse(res["browser_opened"])
        m_lh.assert_not_called()

    def test_lock_released_on_success_so_next_call_wins(self):
        # Two back-to-back wins through the REAL single_flight (temp lock in the hermetic Data dir):
        # the second call could only win if the first released the lock in its finally.
        tokens = {"issued_date_et": etrade._et_today()}
        ps = self._patches()
        for p in ps:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in ps])
        with mock.patch.object(etrade, "_login_headless", return_value=tokens) as m_lh:
            first = etrade.scheduled_reauth("production")
            second = etrade.scheduled_reauth("production")
        self.assertEqual(first["reason"], "reauthed")
        self.assertEqual(second["reason"], "reauthed")
        self.assertEqual(m_lh.call_count, 2)             # lock freed between the two mints


class TestSingleFlightPrimitive(unittest.TestCase):
    """The shared cross-process single-flight guard: one winner, immediate loser, release on exit."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._lock = os.path.join(self._tmp.name, "sf.lock")

    def test_nested_loser_then_release(self):
        with single_flight(self._lock) as won_a:
            self.assertTrue(won_a)                       # first caller wins
            with single_flight(self._lock) as won_b:
                self.assertFalse(won_b)                  # concurrent caller loses, does NOT block
        # holder exited → lock released → a later caller wins again
        with single_flight(self._lock) as won_c:
            self.assertTrue(won_c)

    def test_release_on_exception(self):
        with self.assertRaises(RuntimeError):
            with single_flight(self._lock) as won:
                self.assertTrue(won)
                raise RuntimeError("boom")
        with single_flight(self._lock) as won_again:
            self.assertTrue(won_again)                   # finally-release survived the exception


# ---------------------------------------------------------------------------
# 4. The 3-strike hard block + alert + auth_status surface
# ---------------------------------------------------------------------------

def _state(failures, *, cooldown_until=0.0):
    return {"consecutive_failures": failures, "cooldown_until": cooldown_until, "last_attempt": 0.0}


class TestThreeStrikeHardBlock(unittest.TestCase):
    def test_login_headless_hard_suppressed_at_threshold(self):
        # At the failure threshold the mint is a TRUE stop: no breaker re-arm, no browser, and the
        # throttled alert fires once — even if the cooldown clock happens to be open (cooldown_until=0).
        with mock.patch.object(etrade, "_reauth_alert_threshold", return_value=3), \
             mock.patch.object(etrade, "_load_reauth_state", return_value=_state(3)), \
             mock.patch.object(etrade, "_record_reauth_attempt") as m_arm, \
             mock.patch.object(etrade, "_get_verifier_via_totp") as m_verify, \
             mock.patch.object(etrade.notify, "send_reauth_alert", return_value=True) as m_alert:
            out = etrade._login_headless("ck", "cs", "u", "pw", "production",
                                         headless=True, totp_secret="SECRET")
        self.assertIsNone(out)
        m_arm.assert_not_called()                        # breaker NOT re-armed — hard stop, not retry
        m_verify.assert_not_called()                     # no browser touched
        m_alert.assert_called_once_with("production", "failed")

    def test_below_threshold_not_blocked(self):
        # One under the threshold: NOT blocked → the mint proceeds (breaker arms, browser attempted)
        # and the hard-block alert does NOT fire. Verifier returns "" so nothing is saved.
        with mock.patch.object(etrade, "_reauth_alert_threshold", return_value=3), \
             mock.patch.object(etrade, "_load_reauth_state", return_value=_state(2)), \
             mock.patch.object(etrade, "_record_reauth_attempt") as m_arm, \
             mock.patch.object(etrade, "pyetrade") as pye, \
             mock.patch.object(etrade, "_get_verifier_via_totp", return_value="") as m_verify, \
             mock.patch.object(etrade.notify, "send_reauth_alert") as m_alert:
            pye.ETradeOAuth.return_value.get_request_token.return_value = "http://auth"
            out = etrade._login_headless("ck", "cs", "u", "pw", "production",
                                         headless=True, totp_secret="SECRET")
        self.assertIsNone(out)                            # verifier empty → no tokens
        m_arm.assert_called_once()                        # passed the hard-block gate, tried to mint
        m_verify.assert_called_once()                     # browser path was reached
        m_alert.assert_not_called()                       # streak below threshold → no block alert

    def test_login_headless_alerts_when_failure_crosses_threshold(self):
        # The exact 2 -> arm -> 3 -> fail transition: entry reads 2 (NOT blocked → mint proceeds
        # and the breaker arms, bumping the streak to 3), the mint fails (empty verifier), and the
        # END-of-function alert reads the now-3 streak and fires ONCE. This is how an end-of-day
        # 3rd failure reports immediately, without waiting for a 4th (blocked) attempt.
        st = _state(2)

        def _bump(_env="production"):
            st["consecutive_failures"] += 1

        with mock.patch.object(etrade, "_reauth_alert_threshold", return_value=3), \
             mock.patch.object(etrade, "_load_reauth_state", side_effect=lambda _e="production": dict(st)), \
             mock.patch.object(etrade, "_reauth_cooldown_remaining", return_value=0.0), \
             mock.patch.object(etrade, "_record_reauth_attempt", side_effect=_bump) as m_arm, \
             mock.patch.object(etrade, "pyetrade") as pye, \
             mock.patch.object(etrade, "_get_verifier_via_totp", return_value="") as m_verify, \
             mock.patch.object(etrade.notify, "send_reauth_alert", return_value=True) as m_alert:
            pye.ETradeOAuth.return_value.get_request_token.return_value = "http://auth"
            out = etrade._login_headless("ck", "cs", "u", "pw", "production",
                                         headless=True, totp_secret="SECRET")
        self.assertIsNone(out)
        m_arm.assert_called_once()                        # passed the entry gate (streak was 2) and armed
        m_verify.assert_called_once()                     # browser path was reached
        m_alert.assert_called_once_with("production", "failed")   # end-alert saw the bumped 3

    def test_scheduled_reauth_blocked_reports_and_alerts(self):
        # The automated door, at the hard-block threshold: it opens NO browser, reports the distinct
        # reason "blocked" (not the self-elapsing "breaker"), and re-surfaces the throttled alert.
        ps = [
            mock.patch.object(etrade, "keep_alive", return_value=None),
            mock.patch.object(etrade, "_profile_trust_state", return_value="trusted"),
            mock.patch.object(etrade, "_reauth_cooldown_remaining", return_value=0.0),
            mock.patch.object(etrade, "_breaker_summary", return_value={}),
            mock.patch.object(etrade, "_reauth_alert_threshold", return_value=3),
            mock.patch.object(etrade, "_load_reauth_state", return_value=_state(3)),
        ]
        for p in ps:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in ps])
        with mock.patch.object(etrade, "_login_headless") as m_lh, \
             mock.patch.object(etrade, "_maybe_alert_reauth_blocked") as m_alert:
            res = etrade.scheduled_reauth("production")
        self.assertEqual(res["reason"], "blocked")
        self.assertFalse(res["browser_opened"])
        m_lh.assert_not_called()                          # hard stop — no automated browser
        m_alert.assert_called_once()                      # blocked alert re-surfaced

    def test_reauth_blocked_predicate(self):
        with mock.patch.object(etrade, "_reauth_alert_threshold", return_value=3):
            with mock.patch.object(etrade, "_load_reauth_state", return_value=_state(3)):
                self.assertTrue(etrade._reauth_blocked("production"))
            with mock.patch.object(etrade, "_load_reauth_state", return_value=_state(2)):
                self.assertFalse(etrade._reauth_blocked("production"))

    def test_breaker_summary_blocked_flag(self):
        with mock.patch.object(etrade, "_reauth_alert_threshold", return_value=3), \
             mock.patch.object(etrade, "_load_reauth_state", return_value=_state(3)):
            self.assertTrue(etrade._breaker_summary("production")["blocked"])

    def test_auth_status_reports_blocked_needs_manual(self):
        # The read-only classifier every UI surface (/api/health, /api/etrade/status) calls must
        # report the hard block with its OWN state BLOCKED (distinct from the self-elapsing
        # BREAKER cooldown): needs_manual_auth True, can_auto_reauth False, and breaker.blocked
        # True — so the web badge can paint the red "manual re-auth" message.
        with mock.patch.object(etrade, "_reauth_alert_threshold", return_value=3), \
             mock.patch.object(etrade, "_load_reauth_state", return_value=_state(3)), \
             mock.patch.object(etrade, "_profile_trust_state", return_value="trusted"), \
             mock.patch.object(etrade, "_load_tokens_any_date", return_value=None):
            res = etrade.auth_status("production", probe=False)
        self.assertEqual(res["state"], "blocked")
        self.assertTrue(res["needs_manual_auth"])
        self.assertFalse(res["can_auto_reauth"])
        self.assertTrue(res["breaker"]["blocked"])


if __name__ == "__main__":
    unittest.main()
