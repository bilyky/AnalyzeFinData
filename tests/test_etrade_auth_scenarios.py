"""
Real-scenario tests for the E*TRADE OAuth lifecycle — the ban-safety state machine.

These are HERMETIC: they drive the REAL ``get_tokens`` / ``keep_alive`` code paths and
only mock the four true I/O seams —

    _load_config          (avoid needing real credentials)
    _probe_token_auth     (broker market-clock probe: True / False / None)
    renew_tokens          (OAuth renew HTTP)
    _get_tokens_via_playwright / _login_headless  (the browser choke point)

— plus real token files written to a per-test temp ``_TOKEN_PATH``. ``_load_tokens`` gates
on ``issued_date_et == _et_today()`` (the *real* clock, by design — temporal zero-trust), so
the midnight / weekend scenarios are simulated by writing a token stamped with a prior date.

The whole point is the 2026-08-18 incident: an expired token must NEVER fall through to an
automated browser re-auth loop. Every scenario asserts the ban-safety invariants:
  * the automated path never launches a browser,
  * automated get_tokens/keep_alive fail *soft* to None (never raise),
  * a transient probe blip never destroys an otherwise-good token,
  * only an explicit 401/403 invalidates the cache.

The circuit-breaker mechanics themselves live in test_etrade_reauth_circuit_breaker.py;
this module covers the surrounding lifecycle those tests don't reach.
"""
import datetime
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import aether.etrade as etrade


def _yesterday_et() -> str:
    d = datetime.date.fromisoformat(etrade._et_today())
    return (d - datetime.timedelta(days=1)).isoformat()


class _EtradeScenarioBase(unittest.TestCase):
    """Isolate every auth-state file to a per-test temp dir (prod files never touched)
    and stub _load_config so no real credentials/config are needed."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        d = self._tmp.name

        for const, name in (
            ("_TOKEN_PATH",        "etrade_tokens.json"),
            ("_BROWSER_STATE_PATH", "etrade_browser_state.json"),
            ("_REAUTH_STATE_PATH", "etrade_reauth_state.json"),
        ):
            p = mock.patch.object(etrade, const, os.path.join(d, name))
            p.start()
            self.addCleanup(p.stop)

        cfg = mock.patch.object(etrade, "_load_config",
                                return_value=("ck", "cs", "user", "pw"))
        cfg.start()
        self.addCleanup(cfg.stop)

    # -- helpers -----------------------------------------------------------
    def _write_token(self, issued_date, age_min=0.0, env="production", **extra):
        tok = {
            "oauth_token": "tok", "oauth_token_secret": "sec",
            "env": env, "saved_at": time.time() - age_min * 60,
            "issued_date_et": issued_date,
        }
        tok.update(extra)
        with open(etrade._TOKEN_PATH, "w") as f:
            json.dump(tok, f)
        return tok

    def _make_browser_state(self):
        """Create the saved-browser-state file so os.path.exists() gates open the
        headless path (needed to prove the breaker suppresses it)."""
        with open(etrade._BROWSER_STATE_PATH, "w") as f:
            json.dump({"cookies": []}, f)

    def _no_browser_guard(self):
        """Patch the Playwright entry point AND _login_headless as MagicMocks so any
        scenario can assert the automated browser path was never taken."""
        pw = mock.patch.object(etrade, "_get_tokens_via_playwright")
        lh = mock.patch.object(etrade, "_login_headless")
        m_pw, m_lh = pw.start(), lh.start()
        self.addCleanup(pw.stop)
        self.addCleanup(lh.stop)
        return m_pw, m_lh


class TestTokenLifecycle(_EtradeScenarioBase):
    """A-series: token freshness / calendar boundaries."""

    def test_A1_fresh_same_day_token_returned_no_browser(self):
        # Valid token issued today, <55 min old → renew reuses it, no HTTP, no browser.
        self._write_token(etrade._et_today(), age_min=1.0)
        m_pw, m_lh = self._no_browser_guard()
        with mock.patch.object(etrade, "_probe_token_auth", return_value=True):
            out = etrade.get_tokens("production", allow_browser=False)
        self.assertIsNotNone(out)
        self.assertEqual(out["oauth_token"], "tok")
        m_pw.assert_not_called()
        m_lh.assert_not_called()

    def test_A3_yesterday_token_renewed_survives_midnight(self):
        # Token from yesterday but the broker still accepts it → silent renewal, no browser.
        self._write_token(_yesterday_et(), age_min=90.0)
        m_pw, m_lh = self._no_browser_guard()
        renewed = {"oauth_token": "fresh", "oauth_token_secret": "s2",
                   "env": "production", "issued_date_et": etrade._et_today()}
        with mock.patch.object(etrade, "_probe_token_auth", return_value=True), \
             mock.patch.object(etrade, "renew_tokens", return_value=renewed):
            out = etrade.get_tokens("production", allow_browser=False)
        self.assertEqual(out, renewed)
        m_pw.assert_not_called()
        m_lh.assert_not_called()

    def test_A4_dead_token_not_renewable_returns_none_no_browser(self):
        # Yesterday token, renewal fails, no saved browser state → soft None, NO browser.
        self._write_token(_yesterday_et(), age_min=90.0)
        m_pw, m_lh = self._no_browser_guard()
        with mock.patch.object(etrade, "_probe_token_auth", return_value=None), \
             mock.patch.object(etrade, "renew_tokens", return_value=None):
            out = etrade.get_tokens("production", allow_browser=False)
        self.assertIsNone(out)              # fails soft, never raises
        m_pw.assert_not_called()
        m_lh.assert_not_called()

    def test_A5_weekend_dead_token_keepalive_none_no_renew_no_browser(self):
        # keep_alive sees only a stale-day token → returns None WITHOUT renewing or
        # touching the browser (the anti-ban contract for schedulers over a weekend).
        self._write_token(_yesterday_et(), age_min=90.0)
        m_pw, m_lh = self._no_browser_guard()
        with mock.patch.object(etrade, "renew_tokens") as m_renew, \
             mock.patch.object(etrade, "_probe_token_auth") as m_probe:
            out = etrade.keep_alive("production")
        self.assertIsNone(out)
        m_renew.assert_not_called()         # never even probes/renews an expired token
        m_probe.assert_not_called()
        m_pw.assert_not_called()
        m_lh.assert_not_called()


class TestBrokerRejection(_EtradeScenarioBase):
    """B-series: explicit rejection vs. transient blip."""

    def test_B1_explicit_401_invalidates_cache(self):
        # Broker explicitly rejects (401/403) → token file deleted, soft None, no browser.
        self._write_token(etrade._et_today(), age_min=1.0)
        m_pw, m_lh = self._no_browser_guard()
        with mock.patch.object(etrade, "_probe_token_auth", return_value=False):
            out = etrade.get_tokens("production", allow_browser=False)
        self.assertIsNone(out)
        self.assertFalse(os.path.exists(etrade._TOKEN_PATH))   # cache invalidated
        m_pw.assert_not_called()
        m_lh.assert_not_called()

    def test_B2_transient_blip_keeps_good_token(self):
        # Probe returns None (5xx / network / proxy blip) → the token must NOT be destroyed;
        # renewal is still attempted and, on success, the session survives.
        self._write_token(etrade._et_today(), age_min=90.0)
        m_pw, m_lh = self._no_browser_guard()
        renewed = {"oauth_token": "tok", "oauth_token_secret": "sec",
                   "env": "production", "issued_date_et": etrade._et_today()}
        with mock.patch.object(etrade, "_probe_token_auth", return_value=None), \
             mock.patch.object(etrade, "renew_tokens", return_value=renewed):
            out = etrade.get_tokens("production", allow_browser=False)
        self.assertEqual(out, renewed)
        self.assertTrue(os.path.exists(etrade._TOKEN_PATH))    # good token preserved
        m_pw.assert_not_called()
        m_lh.assert_not_called()


class TestAutomatedBrowserGuards(_EtradeScenarioBase):
    """C-series: the automated browser choke point never runs away."""

    def test_C3_spinner_hang_exception_engages_breaker_soft_none(self):
        # The Akamai soft-block manifests as the Playwright step *raising* (spinner-hang /
        # timeout), not returning None. _login_headless arms the breaker before the browser,
        # so the failure stands (cooldown engaged) even as it swallows the exception and
        # returns None — never propagate, never loop.
        etrade.reset_reauth_circuit_breaker("production")   # gate open
        with mock.patch.object(etrade, "pyetrade") as m_pye, \
             mock.patch.object(etrade, "_get_tokens_via_playwright",
                               side_effect=Exception("Akamai spinner hang")):
            m_pye.ETradeOAuth.return_value.get_request_token.return_value = "http://auth"
            out = etrade._login_headless("ck", "cs", "user", "pw", "production")
        self.assertIsNone(out)
        st = etrade._load_reauth_state("production")
        self.assertEqual(st["consecutive_failures"], 1)               # failure recorded
        self.assertGreater(etrade._reauth_cooldown_remaining("production"), 0)  # backed off

    def test_C5_closed_breaker_suppresses_get_tokens_browser_attempt(self):
        # Dead token + saved browser state EXISTS, but the breaker is cooling down →
        # get_tokens must skip the automated re-auth entirely and fail soft to None.
        self._write_token(_yesterday_et(), age_min=90.0)
        self._make_browser_state()
        etrade._record_reauth_attempt("production")         # engage cooldown
        m_pw, m_lh = self._no_browser_guard()
        with mock.patch.object(etrade, "_probe_token_auth", return_value=None), \
             mock.patch.object(etrade, "renew_tokens", return_value=None):
            out = etrade.get_tokens("production", allow_browser=False)
        self.assertIsNone(out)
        m_lh.assert_not_called()            # breaker pre-check skipped the browser path
        m_pw.assert_not_called()


if __name__ == "__main__":
    unittest.main()
