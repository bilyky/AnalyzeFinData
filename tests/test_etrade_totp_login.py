"""Hermetic, offline tests for the software-TOTP headless E*TRADE login path.

Covers the TOTP mint added on top of the persistent-profile engine — the reboot-proof daily
door that self-completes MFA with a Symantec VIP software code instead of hitting an SMS wall:

  * `_get_verifier_via_totp` types the 6-digit code into E*TRADE's DEDICATED #securityCode field
    (never appended to the password) and returns the scraped OAuth verifier;
  * `_login_headless` routes to the TOTP getter (NOT the legacy Chrome path) whenever a secret is
    configured — including auto-reading CFG.etrade_totp_secret when the arg is omitted — and still
    exchanges + saves through the one breaker/trust choke point;
  * `scheduled_reauth()` treats a TOTP-configured env as trusted (so an 'unseeded' profile no
    longer blocks the door) while the anti-ban circuit breaker STILL gates it.

Every seam is mocked (no E*TRADE contact, no real browser, no pyotp time dependence). Firefox is
faked; nothing launches.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from aether import etrade


def _fake_firefox(verifier="VERIF123", accept_url="https://us.etrade.com/oauth/accept",
                  keystrokes_stick=True):
    """A minimal Playwright(Firefox) double modeling only the seams `_get_verifier_via_totp`
    touches. page.url is a real string; the input-value scrape returns `verifier`.
    Returns (patch_target, browser, context, page).

    `_fill_verified` clears the field (fill("")), TYPES via page.keyboard.type, verifies with
    input_value(), and only if the keystrokes didn't stick sets the value directly with fill(v).
    A shared `_store` echoes whatever last wrote to the field:
      * keystrokes_stick=True  → keyboard.type writes the store (the happy path: typing works,
        so the fill() recovery is NOT exercised — this is the realistic default);
      * keystrokes_stick=False → keyboard.type is a no-op (hydration race drops the keystrokes),
        so ONLY the fill() recovery can make input_value() match — pins the recovery branch."""
    page = mock.MagicMock()
    page.url = accept_url
    _first = page.locator.return_value.first
    _store = {"val": ""}
    _first.fill.side_effect = lambda v, *a, **k: _store.__setitem__("val", v)
    _first.input_value.side_effect = lambda *a, **k: _store["val"]
    if keystrokes_stick:
        page.keyboard.type.side_effect = lambda v, *a, **k: _store.__setitem__("val", v)
    # else: page.keyboard.type stays a no-op MagicMock — keystrokes are dropped.
    _first.is_checked.return_value = True          # already ticked → skip .check()
    # verifier scrape: the getter iterates page.locator(sel).all() and reads each element's
    # value, keeping the first that matches ^[A-Z0-9]{4,10}$. Model one matching input.
    _el = mock.MagicMock()
    _el.get_attribute.return_value = verifier
    page.locator.return_value.all.return_value = [_el]
    context = mock.MagicMock()
    context.new_page.return_value = page
    browser = mock.MagicMock()
    browser.new_context.return_value = context
    p = mock.MagicMock()
    p.firefox.launch.return_value = browser
    fake = mock.MagicMock()
    fake.return_value.__enter__.return_value = p          # `with sync_playwright() as p:`
    return fake, browser, context, page


class TestGetVerifierViaTotp(unittest.TestCase):
    def test_types_totp_into_security_code_field_and_returns_verifier(self):
        fake, browser, ctx, page = _fake_firefox(verifier="ABC987")
        with mock.patch.object(etrade, "sync_playwright", fake), \
             mock.patch("pyotp.TOTP") as m_totp:
            m_totp.return_value.now.return_value = "654321"
            out = etrade._get_verifier_via_totp(
                "http://authorize", "user", "pw", "MYB32SECRET", headless=True)

        self.assertEqual(out, "ABC987")                    # scraped from the Accept-page input
        m_totp.assert_called_once_with("MYB32SECRET")      # code derived from the configured secret
        # The 6-digit code was typed as its OWN keystroke sequence (dedicated #securityCode field),
        # NOT concatenated onto the password.
        typed = [c.args[0] for c in page.keyboard.type.call_args_list]
        self.assertIn("654321", typed)
        self.assertIn("pw", typed)
        self.assertNotIn("pw654321", typed)
        browser.close.assert_called()                      # context always torn down

    def test_skips_stray_input_and_picks_valid_verifier(self):
        # Finding-3 guard: the Accept page can carry hidden/CSRF inputs whose value is NOT an
        # OAuth PIN. The getter must skip anything that fails ^[A-Z0-9]{4,10}$ and take the real one.
        fake, browser, ctx, page = _fake_firefox()
        stray = mock.MagicMock()
        stray.get_attribute.return_value = "csrf-token_not-a-pin=="   # fails the PIN pattern
        good = mock.MagicMock()
        good.get_attribute.return_value = "XY12Z9"                    # valid PIN
        page.locator.return_value.all.return_value = [stray, good]
        with mock.patch.object(etrade, "sync_playwright", fake), \
             mock.patch("pyotp.TOTP") as m_totp:
            m_totp.return_value.now.return_value = "222222"
            out = etrade._get_verifier_via_totp(
                "http://authorize", "user", "pw", "SECRET", headless=True)
        self.assertEqual(out, "XY12Z9")
        browser.close.assert_called()

    def test_returns_none_when_no_verifier_present(self):
        fake, browser, ctx, page = _fake_firefox()
        page.locator.return_value.all.return_value = []    # no input carries a verifier value
        page.url = "https://us.etrade.com/oauth/accept"    # no oauth_verifier= in the URL either
        with mock.patch.object(etrade, "sync_playwright", fake), \
             mock.patch("pyotp.TOTP") as m_totp:
            m_totp.return_value.now.return_value = "111111"
            out = etrade._get_verifier_via_totp(
                "http://authorize", "user", "pw", "SECRET", headless=True)
        self.assertIsNone(out)
        browser.close.assert_called()

    def test_fill_recovers_when_keystrokes_are_dropped(self):
        # The core cold-mint fix: E*TRADE renders a skeleton then HYDRATES, so keystrokes typed
        # into the not-yet-interactive input are silently dropped → empty-form submit → "unable
        # to process". _fill_verified must notice input_value() didn't take and RECOVER via fill().
        # Model dropped keystrokes (keyboard.type is a no-op) so only the fill() recovery can make
        # the fields retain their values — red-green: delete the recovery and this returns None.
        fake, browser, ctx, page = _fake_firefox(verifier="RCV42", keystrokes_stick=False)
        _first = page.locator.return_value.first
        with mock.patch.object(etrade, "sync_playwright", fake), \
             mock.patch("pyotp.TOTP") as m_totp:
            m_totp.return_value.now.return_value = "654321"
            out = etrade._get_verifier_via_totp(
                "http://authorize", "user", "pw", "SECRET", headless=True)
        self.assertEqual(out, "RCV42")                     # got to Accept only via fill() recovery
        filled = [c.args[0] for c in _first.fill.call_args_list]
        self.assertIn("user", filled)                      # each field recovered by direct fill()
        self.assertIn("pw", filled)
        self.assertIn("654321", filled)

    def test_returns_none_when_a_field_never_retains_input(self):
        # If a field stays empty even after the fill() recovery (never hydrates / not actionable),
        # _fill_verified returns False and the getter BAILS at the user field — it must not blunder
        # on to submit an empty form. Model a permanently-dead field: nothing ever sticks.
        fake, browser, ctx, page = _fake_firefox(keystrokes_stick=False)
        _first = page.locator.return_value.first
        _first.fill.side_effect = None                     # fill() no longer writes the store…
        _first.input_value.side_effect = None
        _first.input_value.return_value = ""               # …so the field reads empty forever
        with mock.patch.object(etrade, "sync_playwright", fake), \
             mock.patch("pyotp.TOTP") as m_totp:
            m_totp.return_value.now.return_value = "111111"
            out = etrade._get_verifier_via_totp(
                "http://authorize", "user", "pw", "SECRET", headless=True)
        self.assertIsNone(out)                             # bailed before submitting
        page.click.assert_not_called()                     # never reached submit / Accept
        browser.close.assert_called()                      # context still torn down


class TestLoginHeadlessTotpRouting(unittest.TestCase):
    def _exchange_patches(self):
        return {
            "cool": mock.patch.object(etrade, "_reauth_cooldown_remaining", return_value=0.0),
            "arm": mock.patch.object(etrade, "_record_reauth_attempt"),
            "reset": mock.patch.object(etrade, "reset_reauth_circuit_breaker"),
            "trust": mock.patch.object(etrade, "_set_profile_trust"),
            "save": mock.patch.object(etrade, "_save_tokens"),
            "pye": mock.patch.object(etrade, "pyetrade"),
        }

    def test_explicit_secret_uses_totp_path_not_chrome(self):
        patches = self._exchange_patches()
        started = {k: p.start() for k, p in patches.items()}
        self.addCleanup(lambda: [p.stop() for p in patches.values()])
        started["pye"].ETradeOAuth.return_value.get_request_token.return_value = "http://auth"
        started["pye"].ETradeOAuth.return_value.get_access_token.return_value = {"oauth_token": "t"}
        with mock.patch.object(etrade, "_get_verifier_via_totp", return_value="V") as m_totp, \
             mock.patch.object(etrade, "_get_tokens_via_playwright") as m_chrome:
            out = etrade._login_headless("ck", "cs", "u", "pw", "production",
                                         headless=True, totp_secret="SECRET")
        self.assertEqual(out, {"oauth_token": "t"})
        m_totp.assert_called_once()                        # TOTP getter drove the mint
        m_chrome.assert_not_called()                       # legacy Chrome path NOT used
        started["save"].assert_called_once()               # token persisted
        started["reset"].assert_called_once_with("production")   # breaker retracted on success

    def test_omitted_secret_auto_reads_cfg(self):
        patches = self._exchange_patches()
        started = {k: p.start() for k, p in patches.items()}
        self.addCleanup(lambda: [p.stop() for p in patches.values()])
        started["pye"].ETradeOAuth.return_value.get_request_token.return_value = "http://auth"
        started["pye"].ETradeOAuth.return_value.get_access_token.return_value = {"oauth_token": "t"}
        with mock.patch.object(etrade.CFG, "etrade_totp_secret", "CFGSECRET"), \
             mock.patch.object(etrade, "_get_verifier_via_totp", return_value="V") as m_totp, \
             mock.patch.object(etrade, "_get_tokens_via_playwright") as m_chrome:
            etrade._login_headless("ck", "cs", "u", "pw", "production", headless=True)
        m_totp.assert_called_once()                        # picked up the CFG secret with no arg
        m_chrome.assert_not_called()

    def test_no_secret_falls_back_to_chrome_path(self):
        patches = self._exchange_patches()
        started = {k: p.start() for k, p in patches.items()}
        self.addCleanup(lambda: [p.stop() for p in patches.values()])
        started["pye"].ETradeOAuth.return_value.get_request_token.return_value = "http://auth"
        started["pye"].ETradeOAuth.return_value.get_access_token.return_value = {"oauth_token": "t"}
        with mock.patch.object(etrade.CFG, "etrade_totp_secret", ""), \
             mock.patch.object(etrade, "_get_verifier_via_totp") as m_totp, \
             mock.patch.object(etrade, "_get_tokens_via_playwright", return_value="V") as m_chrome:
            etrade._login_headless("ck", "cs", "u", "pw", "production", headless=True)
        m_chrome.assert_called_once()                      # no secret → legacy device-trust path
        m_totp.assert_not_called()


class TestScheduledReauthTotpTrustBypass(unittest.TestCase):
    """With TOTP configured, an 'unseeded' profile no longer blocks the door — but the breaker does."""

    def _patches(self, *, alive=None, trust="unseeded", cooldown=0.0):
        return {
            "keep_alive": mock.patch.object(etrade, "keep_alive", return_value=alive),
            "trust": mock.patch.object(etrade, "_profile_trust_state", return_value=trust),
            "cooldown": mock.patch.object(etrade, "_reauth_cooldown_remaining", return_value=cooldown),
            "cfg": mock.patch.object(etrade, "_load_config", return_value=("ck", "cs", "u", "pw")),
            "breaker": mock.patch.object(etrade, "_breaker_summary", return_value={}),
            "set_trust": mock.patch.object(etrade, "_set_profile_trust"),
            "totp": mock.patch.object(etrade.CFG, "etrade_totp_secret", "SECRET"),
        }

    def _run(self, patches, login_headless):
        [p.start() for p in patches.values()]
        self.addCleanup(lambda: [p.stop() for p in patches.values()])
        lh = mock.patch.object(etrade, "_login_headless", **login_headless)
        m_lh = lh.start()
        self.addCleanup(lh.stop)
        return etrade.scheduled_reauth("production"), m_lh

    def test_totp_configured_unseeded_still_reauths(self):
        tokens = {"issued_date_et": etrade._et_today()}
        res, m_lh = self._run(self._patches(trust="unseeded"), {"return_value": tokens})
        self.assertTrue(res["ok"])
        self.assertEqual(res["reason"], "reauthed")
        self.assertTrue(res["browser_opened"])
        m_lh.assert_called_once()                          # trust gate bypassed by TOTP

    def test_totp_configured_still_respects_breaker(self):
        res, m_lh = self._run(self._patches(trust="unseeded", cooldown=600.0),
                              {"return_value": None})
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "breaker")
        self.assertFalse(res["browser_opened"])
        m_lh.assert_not_called()                           # ban-safety: breaker still gates


if __name__ == "__main__":
    unittest.main()
