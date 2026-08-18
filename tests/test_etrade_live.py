"""
LIVE E*TRADE tier — opt-in, ban-SAFE, HTTP-only.

These tests actually contact the real broker, so they are gated behind
``AETHER_LIVE_TESTS=1`` and SKIP by default. When that env var is set, the
tests/__init__.py hermeticity guard is disabled, so these see the REAL token
files under Data/ and a real network.

Ban-safety contract enforced here (this is the whole reason the tier is narrow):
  * Only PURE-HTTP paths run — ``_probe_token_auth`` (a GET on the public market
    clock) and ``keep_alive`` (the renew endpoint). Both are 100% ban-safe.
  * A browser is NEVER launched. Each test asserts the Playwright entry points were
    not called; a regression that made the automated path open a browser fails loudly.
  * No same-day token is NOT a failure — it is the correct expired-session contract.
    The test SKIPS with the human recovery instruction instead of going red.

The genuinely ban-SENSITIVE step — creating a NEW session via the interactive
browser login — is intentionally NOT automated here. Run it by hand from a clean
context (ideally a different IP):

    python scripts/diagnostics/test_etrade.py production

Run this tier with:

    AETHER_LIVE_TESTS=1 python -m unittest tests.test_etrade_live -v
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import aether.etrade as etrade


@unittest.skipUnless(
    os.getenv("AETHER_LIVE_TESTS"),
    "live E*TRADE tier — set AETHER_LIVE_TESTS=1 to run (contacts the real broker)",
)
class TestEtradeLive(unittest.TestCase):
    def setUp(self):
        # Belt-and-suspenders: even with the guard disabled, fail hard if any live path
        # tries to open a browser. keep_alive/probe must never reach these.
        for name in ("_get_tokens_via_playwright", "_login_headless"):
            p = mock.patch.object(
                etrade, name,
                side_effect=AssertionError(f"BAN-SAFETY VIOLATION: {name} called in a live HTTP-only test"),
            )
            p.start()
            self.addCleanup(p.stop)

    def test_probe_token_auth_is_tristate_and_never_raises(self):
        """The market-clock probe returns a clean tri-state against the real endpoint."""
        tokens = etrade._load_tokens_any_date("production")
        if not tokens:
            self.skipTest("No cached production token to probe — run test_etrade.py first.")
        result = etrade._probe_token_auth(tokens, "production")
        self.assertIn(result, (True, False, None))  # authorized / rejected / transient

    def test_keep_alive_renew_is_ban_safe_and_soft(self):
        """keep_alive renews a live same-day token over pure HTTP, or fails soft to None
        when the session is expired — never a browser, never an exception."""
        tokens = etrade.keep_alive("production")
        if tokens is None:
            self.skipTest(
                "No valid same-day E*TRADE session (expired/weekend). This is the correct "
                "contract, not a failure. Re-auth from a clean context / different IP: "
                "python scripts/diagnostics/test_etrade.py production"
            )
        self.assertIn("oauth_token", tokens)
        self.assertIn("oauth_token_secret", tokens)
        self.assertEqual(tokens.get("issued_date_et"), etrade._et_today())


if __name__ == "__main__":
    unittest.main()
