"""Hermetic tests for the BrowserStateStore port wiring in the Playwright login.

The persistent-Chrome login path (`_get_tokens_via_playwright`) used to save the
trusted-device cookies with an inline `os.makedirs` + `open(_BROWSER_STATE_PATH)`
+ `json.dump`. That write is now routed through the `BrowserStateStore` port
(`make_etrade_store().browser_state.save(...)`) so the persistence backend is
swappable (file today, DB later) without touching the ban-sensitive login flow.
The file adapter is behaviour-identical to the old inline write, so this is a pure
seam extraction — these tests pin the seam:

  * on a successful auth (verifier captured) the browser state is saved through the
    port exactly once, with the dict `ctx.storage_state()` returns — and NOT via a
    raw file write bypassing the port;
  * a port `save` failure is swallowed (matches the old inline `except`) and never
    breaks the returned verifier;
  * when no verifier is captured, the save is NOT attempted (the `if verifier:`
    guard is preserved).

Everything is faked: no real Playwright, no browser, no disk. `sync_playwright`,
`make_etrade_store`, and `os.makedirs` are patched on the live module.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from aether import etrade


def _fake_chromium(storage_state, verifier="RCV42"):
    """A minimal Playwright(Chromium persistent-context) double for the seams
    `_get_tokens_via_playwright` touches.

    * `p.chromium.launch_persistent_context(...)` returns the context;
    * `ctx.pages[0]` is the page; `ctx.storage_state()` returns `storage_state`;
    * `_try_read_verifier` scrapes the code from `query_selector("input[readonly]")`
      whose `get_attribute("value")` yields a value matching ^[A-Z0-9]{4,10}$, so
      the first poll iteration captures the verifier and the flow exits cleanly.

    Pass `verifier=None` to model "code never captured" (query_selector returns
    nothing that matches) so the guard around the save can be pinned.

    Returns (patch_target, context, page).
    """
    page = mock.MagicMock()
    # A real, non-OTP URL so the `"sendotpcode"/"otp" in page.url` MFA branch is skipped.
    page.url = "https://us.etrade.com/e/t/user/consent"
    el = mock.MagicMock()
    el.get_attribute.return_value = verifier
    # Only the readonly-input selector yields the code; every other lookup misses.
    page.query_selector.side_effect = (
        lambda sel: el if (verifier and sel == "input[readonly]") else None
    )

    ctx = mock.MagicMock()
    ctx.pages = [page]
    ctx.storage_state.return_value = storage_state

    p = mock.MagicMock()
    p.chromium.launch_persistent_context.return_value = ctx

    fake = mock.MagicMock()
    fake.return_value.__enter__.return_value = p          # `with sync_playwright() as p:`
    return fake, ctx, page


def _fake_store():
    """A store double whose browser_state.save is a MagicMock to assert on."""
    store = mock.MagicMock()
    store.browser_state.save = mock.MagicMock()
    return store


class TestBrowserStatePortWiring(unittest.TestCase):
    def test_save_routes_through_port_with_storage_state(self):
        sentinel_state = {"cookies": [{"name": "trust", "value": "abc"}], "origins": []}
        fake_sp, ctx, _page = _fake_chromium(sentinel_state, verifier="RCV42")
        store = _fake_store()
        with mock.patch.object(etrade, "sync_playwright", fake_sp), \
             mock.patch.object(etrade, "make_etrade_store", return_value=store), \
             mock.patch.object(etrade.os, "makedirs"), \
             mock.patch.object(etrade, "_log", mock.MagicMock()):
            out = etrade._get_tokens_via_playwright(
                "http://authorize", "user", "pw", headless=True)

        self.assertEqual(out, "RCV42")                     # verifier flowed back
        # The trusted-device cookies were persisted through the port, exactly once,
        # with the dict Playwright handed us — no inline file write.
        store.browser_state.save.assert_called_once_with(sentinel_state)
        ctx.storage_state.assert_called_once()

    def test_port_save_failure_is_swallowed(self):
        # The old inline write caught Exception; the port swap must keep that — a
        # persistence hiccup cannot cost us the freshly minted verifier.
        fake_sp, _ctx, _page = _fake_chromium({"cookies": []}, verifier="ABC42")
        store = _fake_store()
        store.browser_state.save.side_effect = OSError("disk full")
        with mock.patch.object(etrade, "sync_playwright", fake_sp), \
             mock.patch.object(etrade, "make_etrade_store", return_value=store), \
             mock.patch.object(etrade.os, "makedirs"), \
             mock.patch.object(etrade, "_log", mock.MagicMock()):
            out = etrade._get_tokens_via_playwright(
                "http://authorize", "user", "pw", headless=True)

        self.assertEqual(out, "ABC42")                     # save blew up; verifier survived
        store.browser_state.save.assert_called_once()

    def test_no_save_when_verifier_not_captured(self):
        # verifier never captured → the `if verifier:` guard must skip the save.
        # The flow then falls through to manual entry; feed it a code via input().
        fake_sp, _ctx, _page = _fake_chromium({"cookies": []}, verifier=None)
        store = _fake_store()
        with mock.patch.object(etrade, "sync_playwright", fake_sp), \
             mock.patch.object(etrade, "make_etrade_store", return_value=store), \
             mock.patch.object(etrade.os, "makedirs"), \
             mock.patch.object(etrade.sys.stdin, "isatty", return_value=True), \
             mock.patch("builtins.input", return_value="MANUAL1"), \
             mock.patch.object(etrade, "_log", mock.MagicMock()):
            out = etrade._get_tokens_via_playwright(
                "http://authorize", "user", "pw", headless=True)

        self.assertEqual(out, "MANUAL1")                   # manual fallback code
        store.browser_state.save.assert_not_called()       # guard held — no save attempted


if __name__ == "__main__":
    unittest.main()
