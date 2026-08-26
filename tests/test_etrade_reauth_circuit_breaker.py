"""Anti-ban guards for E*TRADE automated re-authentication.

Red-green anchors (all three are NEW symbols; on pre-fix code they don't exist, so
this whole module fails to run — that is the "red"):
  - the escalating circuit breaker (_record_reauth_attempt / _reauth_cooldown_remaining /
    reset_reauth_circuit_breaker) that throttles automated browser re-auth after a
    failure, so a stale-session Akamai hang can't be hammered into an IP ban;
  - _login_headless honoring that breaker (the SINGLE choke point — no browser launch
    while the gate is closed) and arming it BEFORE the browser opens, so an attempt that
    is killed/hung mid-browser still leaves the breaker engaged;
  - keep_alive() being renew-ONLY: it never opens a browser and makes zero brokerage
    calls once the token has expired.
"""
import os
import tempfile
import unittest
from unittest import mock

from aether import etrade


class _StateFileMixin:
    """Point the breaker at a throwaway state file so tests never touch real Data/."""

    def setUp(self):
        fd, self._state_path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.unlink(self._state_path)          # start with NO file -> gate fully open
        p = mock.patch.object(etrade, "_REAUTH_STATE_PATH", self._state_path)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(
            lambda: os.path.exists(self._state_path) and os.unlink(self._state_path)
        )
        # A successful _login_headless now (re-)proves device trust via _set_profile_trust,
        # which writes _TRUST_MARKER_PATH. Redirect it to a throwaway temp file so the success
        # test never leaks a bogus 'trusted' marker into the real Data/ (a stray marker there
        # would let production's scheduled_reauth open an unattended browser with no real seed).
        fd2, self._trust_path = tempfile.mkstemp(suffix=".trust.json")
        os.close(fd2)
        os.unlink(self._trust_path)
        tp = mock.patch.object(etrade, "_TRUST_MARKER_PATH", self._trust_path)
        tp.start()
        self.addCleanup(tp.stop)
        self.addCleanup(
            lambda: os.path.exists(self._trust_path) and os.unlink(self._trust_path)
        )


class TestCircuitBreakerBackoff(_StateFileMixin, unittest.TestCase):
    def test_gate_open_when_no_state(self):
        self.assertEqual(etrade._reauth_cooldown_remaining(), 0.0)

    def test_escalating_backoff(self):
        # Each _record_reauth_attempt() is one browser re-auth starting; with no success to
        # retract it, N calls == N consecutive failures. Backoff doubles 15→30→60→120→240; the
        # 6 h (360 min) cap holds only for the first four. At the 5th consecutive failure the
        # deep-failure ceiling lifts to 24 h (1440 min), so the exponential keeps climbing
        # (480, 960) then pins at 1440 — a sustained streak means a human must re-auth to clear.
        expected_min = [15, 30, 60, 120, 240, 480, 960, 1440, 1440]
        with mock.patch.object(etrade.time, "time", return_value=1000.0):
            for i, mins in enumerate(expected_min, start=1):
                etrade._record_reauth_attempt()
                st = etrade._load_reauth_state()
                self.assertEqual(st["consecutive_failures"], i)
                self.assertAlmostEqual(st["cooldown_until"], 1000.0 + mins * 60, places=3)

    def test_remaining_counts_down_then_opens(self):
        with mock.patch.object(etrade.time, "time", return_value=1000.0):
            etrade._record_reauth_attempt()                   # cooldown until 1900
        with mock.patch.object(etrade.time, "time", return_value=1000.0 + 5 * 60):
            self.assertAlmostEqual(etrade._reauth_cooldown_remaining(), 10 * 60, places=0)
        with mock.patch.object(etrade.time, "time", return_value=1000.0 + 20 * 60):
            self.assertEqual(etrade._reauth_cooldown_remaining(), 0.0)

    def test_success_resets_the_breaker(self):
        with mock.patch.object(etrade.time, "time", return_value=1000.0):
            etrade._record_reauth_attempt()
            etrade._record_reauth_attempt()
        self.assertEqual(etrade._load_reauth_state()["consecutive_failures"], 2)
        etrade.reset_reauth_circuit_breaker()                 # success retracts the arm
        st = etrade._load_reauth_state()
        self.assertEqual(st["consecutive_failures"], 0)
        self.assertEqual(st["cooldown_until"], 0.0)

    def test_reset_helper_clears(self):
        with mock.patch.object(etrade.time, "time", return_value=1000.0):
            etrade._record_reauth_attempt()
            self.assertGreater(etrade._reauth_cooldown_remaining(), 0)   # still within cooldown
        etrade.reset_reauth_circuit_breaker()
        self.assertEqual(etrade._reauth_cooldown_remaining(), 0.0)


class TestBreakerStateIsPerEnv(_StateFileMixin, unittest.TestCase):
    """A sandbox/test re-auth failure must NEVER engage the PRODUCTION breaker
    (test/prod separation). Env state files are derived from the canonical path, so the
    mixin's tempfile redirect isolates every env together — nothing hits real Data/."""

    def test_sandbox_failure_does_not_cool_down_production(self):
        with mock.patch.object(etrade.time, "time", return_value=1000.0):
            etrade._record_reauth_attempt("sandbox")           # trip the SANDBOX breaker
            # Sandbox is cooling down…
            self.assertGreater(etrade._reauth_cooldown_remaining("sandbox"), 0)
            # …but production is untouched — its gate stays fully open.
            self.assertEqual(etrade._reauth_cooldown_remaining("production"), 0.0)
        self.assertEqual(etrade._load_reauth_state("production")["consecutive_failures"], 0)

    def test_env_paths_are_distinct_and_isolated_to_temp(self):
        prod_path    = etrade._reauth_state_path("production")
        sandbox_path = etrade._reauth_state_path("sandbox")
        self.assertNotEqual(prod_path, sandbox_path)
        self.assertEqual(prod_path, self._state_path)          # production == canonical (patched)
        # sandbox file lives beside the patched prod file (the temp dir), never in real Data/.
        self.assertEqual(os.path.dirname(sandbox_path), os.path.dirname(self._state_path))


class TestLoginHeadlessHonorsBreaker(_StateFileMixin, unittest.TestCase):
    def test_closed_gate_suppresses_browser(self):
        with mock.patch.object(etrade.time, "time", return_value=1000.0):
            etrade._record_reauth_attempt()                   # cooldown until 1900
        with mock.patch.object(etrade.time, "time", return_value=1000.0 + 60), \
             mock.patch.object(etrade, "_get_tokens_via_playwright") as pw_launch, \
             mock.patch.object(etrade, "pyetrade") as pye:
            result = etrade._login_headless("ck", "cs", "u", "pw", "production")
        self.assertIsNone(result)
        pw_launch.assert_not_called()          # no browser opened while cooling down
        pye.ETradeOAuth.assert_not_called()
        # The gate is checked BEFORE the arm: a suppressed call must NOT escalate the
        # backoff (still 1 from the setup arm). If the arm ran ahead of the gate, a cooling
        # breaker would keep climbing toward the 24 h lockout while doing nothing — a
        # self-inflicted ban. This pins the ordering invariant.
        self.assertEqual(etrade._load_reauth_state()["consecutive_failures"], 1)

    def test_failure_arms_breaker_before_browser_and_stands_after(self):
        # The red-green anchor for the whole fix: a no-verifier browser step (the Akamai
        # spinner-hang shape) proves BOTH halves in one path. We spy the breaker state from
        # INSIDE the browser step, then re-check after it returns:
        #   (1) armed BEFORE the browser — failures==1, cooldown>0 by the time the browser is
        #       entered, so a process killed/hung right there still leaves the breaker engaged
        #       (on the old record-AFTER code the count is still 0 here — this is the "red"); and
        #   (2) the browser WAS entered and the arm STANDS after the failed attempt returns.
        seen = {}

        def _spy(*a, **k):
            seen["failures"] = etrade._load_reauth_state("production")["consecutive_failures"]
            seen["cooldown"] = etrade._reauth_cooldown_remaining("production")
            return None   # simulate the hang/no-verifier failure

        with mock.patch.object(etrade, "_get_tokens_via_playwright", side_effect=_spy) as pw_launch, \
             mock.patch.object(etrade, "pyetrade") as pye:
            pye.ETradeOAuth.return_value.get_request_token.return_value = "http://auth"
            result = etrade._login_headless("ck", "cs", "u", "pw", "production")
        self.assertIsNone(result)
        pw_launch.assert_called_once()                     # the browser step was actually entered
        self.assertEqual(seen.get("failures"), 1)          # armed before the browser, not after
        self.assertGreater(seen.get("cooldown", 0), 0)     # cooldown already engaged at that point
        self.assertEqual(etrade._load_reauth_state()["consecutive_failures"], 1)  # arm stands after
        self.assertGreater(etrade._reauth_cooldown_remaining(), 0)                # will not hammer

    def test_open_gate_success_returns_and_resets(self):
        # Pre-load a failure to prove a SUCCESS clears it.
        with mock.patch.object(etrade.time, "time", return_value=1000.0):
            etrade._record_reauth_attempt()
        fake_tokens = {"oauth_token": "t", "oauth_token_secret": "s"}
        with mock.patch.object(etrade, "_get_tokens_via_playwright", return_value="verifier"), \
             mock.patch.object(etrade, "_save_tokens") as save, \
             mock.patch.object(etrade, "pyetrade") as pye:
            pye.ETradeOAuth.return_value.get_request_token.return_value = "http://auth"
            pye.ETradeOAuth.return_value.get_access_token.return_value = fake_tokens
            result = etrade._login_headless("ck", "cs", "u", "pw", "production")
        self.assertEqual(result, fake_tokens)
        save.assert_called_once()
        self.assertEqual(etrade._reauth_cooldown_remaining(), 0.0)   # breaker reset on success
        self.assertEqual(etrade._load_reauth_state()["consecutive_failures"], 0)


class TestKeepAliveIsRenewOnly(unittest.TestCase):
    def test_no_token_returns_none_and_makes_no_call(self):
        with mock.patch.object(etrade, "_load_tokens", return_value=None), \
             mock.patch.object(etrade, "_probe_token_auth") as probe, \
             mock.patch.object(etrade, "renew_tokens") as renew:
            self.assertIsNone(etrade.keep_alive("production"))
        probe.assert_not_called()             # zero brokerage contact on a dead token
        renew.assert_not_called()

    def test_rejected_token_returns_none(self):
        with mock.patch.object(etrade, "_load_tokens", return_value={"oauth_token": "t"}), \
             mock.patch.object(etrade, "_probe_token_auth", return_value=False), \
             mock.patch.object(etrade, "renew_tokens") as renew:
            self.assertIsNone(etrade.keep_alive("production"))
        renew.assert_not_called()

    def test_valid_token_is_renewed(self):
        toks, renewed = {"oauth_token": "t"}, {"oauth_token": "t2"}
        with mock.patch.object(etrade, "_load_tokens", return_value=toks), \
             mock.patch.object(etrade, "_probe_token_auth", return_value=True), \
             mock.patch.object(etrade, "renew_tokens", return_value=renewed) as renew:
            self.assertIs(etrade.keep_alive("production"), renewed)
        renew.assert_called_once()


if __name__ == "__main__":
    unittest.main()
