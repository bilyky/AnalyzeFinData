"""Hermetic, offline tests for the daily automatic E*TRADE re-auth door.

These cover the NEW zero-touch scheduled path added on top of the persistent-profile engine:

  * the OTP-wall detector (`_get_tokens_via_playwright` raising `SmsRequired` in headless mode
    WITHOUT clicking "Send Code" or waiting — no real SMS, no hang);
  * `_login_headless` treating `SmsRequired` as NOT a ban-risk failure: it CLEARS the pre-armed
    circuit breaker (rather than escalating it) and re-raises with the env attached;
  * the `scheduled_reauth()` state machine — the ONE automated browser door — proving the
    ban-safety invariant: it opens a browser ONLY when the token is dead AND the persistent
    profile is `trusted` AND the breaker is open; on `unseeded` / `sms_required` it opens NO
    browser at all, and on a headless OTP wall it latches the trust marker to `sms_required`;
  * the throttled re-auth alert (email + desktop push) firing once per episode and re-arming
    after the next successful mint, and `send_desktop_push` never raising.

Everything here is mocked at the true I/O seams (no E*TRADE contact, no real browser, no SMTP,
no PowerShell). The circuit-breaker mechanics live in test_etrade_reauth_circuit_breaker.py.
"""
import contextlib
import datetime
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tests  # globally locks hermeticity and redirects prod Data/ to temp
from aether import etrade
import server
from aether import notify


# ---------------------------------------------------------------------------
# 1. The OTP-wall detector: headless abort vs. headed human flow
# ---------------------------------------------------------------------------

def _fake_playwright(otp_url="https://us.etrade.com/e/t/user/sendotpcode"):
    """A minimal Playwright double whose page sits on the OTP wall.

    Only the seams `_get_tokens_via_playwright` actually touches are modeled; page.url is a
    real string (so `"sendotpcode" in page.url` and `page.url.lower()` work) pinned to the OTP
    page, so control reaches the MFA branch. Returns (patch_target, ctx, page)."""
    page = mock.MagicMock()
    page.url = otp_url
    ctx = mock.MagicMock()
    ctx.pages = [page]
    p = mock.MagicMock()
    p.chromium.launch_persistent_context.return_value = ctx
    fake = mock.MagicMock()
    fake.return_value.__enter__.return_value = p       # `with sync_playwright() as p:`
    return fake, ctx, page


class TestOtpWallDetection(unittest.TestCase):
    def test_headless_otp_raises_sms_required_without_sending_or_waiting(self):
        # The core ban-safety behavior: an unattended run that hits the OTP wall must abort
        # INSTANTLY — never click "Send Code" (a real SMS for nothing) and never enter the
        # 2-minute OTP wait loop (which would hang the scheduler).
        fake, ctx, page = _fake_playwright()
        with mock.patch.object(etrade, "sync_playwright", fake):
            with self.assertRaises(etrade.SmsRequired):
                etrade._get_tokens_via_playwright("http://auth", "user", "pw", headless=True)
        ctx.close.assert_called()                       # context torn down on the way out
        page.wait_for_timeout.assert_not_called()       # NO OTP wait loop == no hang

    def test_headed_otp_does_not_raise_and_waits_for_human(self):
        # With a human present (headless=False) the behavior is unchanged: it does NOT raise
        # SmsRequired — it clicks Send Code and enters the wait loop (wait_for_timeout fires),
        # then falls through to the normal verifier-capture path (which, in this no-TTY test env,
        # ends in the manual-entry fallback — any non-SmsRequired outcome is fine here).
        fake, ctx, page = _fake_playwright()
        with mock.patch.object(etrade, "sync_playwright", fake):
            try:
                etrade._get_tokens_via_playwright("http://auth", "user", "pw", headless=False)
            except etrade.SmsRequired:
                self.fail("headed OTP must NOT raise SmsRequired — that's the automated path")
            except Exception:
                pass                                     # no-TTY manual-entry fallback, expected
        page.wait_for_timeout.assert_called()            # it waited for the human to enter OTP


# ---------------------------------------------------------------------------
# 2. _login_headless treats SmsRequired as trust-lapse, not a ban-risk failure
# ---------------------------------------------------------------------------

class TestLoginHeadlessSmsHandling(unittest.TestCase):
    def test_sms_required_clears_breaker_and_reraises_with_env(self):
        # Reaching the OTP page means the browser + login WORKED — trust merely lapsed. That is
        # not the Akamai-hang ban shape, so the pre-armed breaker must be RETRACTED (not left to
        # escalate toward the 24h lockout) and the signal propagated to the caller.
        with mock.patch.object(etrade, "_load_config", return_value=("ck", "cs", "u", "pw")), \
             mock.patch.object(etrade, "pyetrade") as pye, \
             mock.patch.object(etrade, "_reauth_cooldown_remaining", return_value=0.0), \
             mock.patch.object(etrade, "_record_reauth_attempt") as arm, \
             mock.patch.object(etrade, "reset_reauth_circuit_breaker") as reset, \
             mock.patch.object(etrade, "_get_tokens_via_playwright",
                               side_effect=etrade.SmsRequired()):
            pye.ETradeOAuth.return_value.get_request_token.return_value = "http://auth"
            with self.assertRaises(etrade.SmsRequired) as cm:
                etrade._login_headless("ck", "cs", "u", "pw", "production", headless=True)
        arm.assert_called_once()                # breaker was pre-armed before the browser
        reset.assert_called_once_with("production")   # …then CLEARED, not escalated
        self.assertEqual(cm.exception.env, "production")   # env attached for the caller/alert


# ---------------------------------------------------------------------------
# 3. scheduled_reauth() — the state machine / the one automated door
# ---------------------------------------------------------------------------

class TestScheduledReauth(unittest.TestCase):
    """Every case mocks the I/O seams so no browser, broker, or file is touched."""

    def _patches(self, *, alive=None, trust="trusted", cooldown=0.0):
        return {
            "keep_alive": mock.patch.object(etrade, "keep_alive", return_value=alive),
            "trust": mock.patch.object(etrade, "_profile_trust_state", return_value=trust),
            "cooldown": mock.patch.object(etrade, "_reauth_cooldown_remaining", return_value=cooldown),
            "cfg": mock.patch.object(etrade, "_load_config", return_value=("ck", "cs", "u", "pw")),
            "breaker": mock.patch.object(etrade, "_breaker_summary", return_value={}),
            "set_trust": mock.patch.object(etrade, "_set_profile_trust"),
        }

    def _run(self, patches, login_headless):
        started = [p.start() for p in patches.values()]
        self.addCleanup(lambda: [p.stop() for p in patches.values()])
        lh = mock.patch.object(etrade, "_login_headless", **login_headless)
        m_lh = lh.start()
        self.addCleanup(lh.stop)
        return etrade.scheduled_reauth("production"), m_lh, patches

    def test_live_token_renews_with_no_browser(self):
        alive = {"issued_date_et": etrade._et_today()}
        res, m_lh, _ = self._run(self._patches(alive=alive),
                                  {"return_value": None})
        self.assertTrue(res["ok"])
        self.assertEqual(res["reason"], "renewed")
        self.assertFalse(res["browser_opened"])
        m_lh.assert_not_called()                # renew-first: no browser when a token is alive

    def test_unseeded_profile_opens_no_browser(self):
        res, m_lh, _ = self._run(self._patches(alive=None, trust="unseeded"),
                                 {"return_value": None})
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "unseeded")
        self.assertFalse(res["browser_opened"])
        m_lh.assert_not_called()                # BAN-SAFETY: never a browser while untrusted

    def test_sms_required_marker_opens_no_browser(self):
        res, m_lh, _ = self._run(self._patches(alive=None, trust="sms_required"),
                                 {"return_value": None})
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "sms_required")
        self.assertFalse(res["browser_opened"])
        m_lh.assert_not_called()                # latched off until a human re-seeds

    def test_cooling_breaker_opens_no_browser(self):
        res, m_lh, _ = self._run(self._patches(alive=None, trust="trusted", cooldown=600.0),
                                 {"return_value": None})
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "breaker")
        self.assertFalse(res["browser_opened"])
        m_lh.assert_not_called()                # don't reopen while the breaker is cooling

    def test_trusted_dead_token_reauths(self):
        tokens = {"issued_date_et": etrade._et_today()}
        res, m_lh, _ = self._run(self._patches(alive=None, trust="trusted"),
                                 {"return_value": tokens})
        self.assertTrue(res["ok"])
        self.assertEqual(res["reason"], "reauthed")
        self.assertTrue(res["browser_opened"])
        m_lh.assert_called_once()               # the ONE allowed automated browser open

    def test_trusted_headless_otp_latches_sms_required(self):
        # The monthly moment: trust lapsed, the headless run hits the OTP wall → the marker is
        # flipped to sms_required so NO further automated browser opens until a human bootstraps.
        patches = self._patches(alive=None, trust="trusted")
        res, m_lh, p = self._run(patches, {"side_effect": etrade.SmsRequired("production")})
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "sms_required")
        self.assertTrue(res["browser_opened"])          # one browser hit the wall, then latched
        etrade._set_profile_trust.assert_called_once_with("production", "sms_required")

    def test_trusted_dead_token_failed_reauth(self):
        res, m_lh, _ = self._run(self._patches(alive=None, trust="trusted"),
                                 {"return_value": None})
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "failed")
        self.assertTrue(res["browser_opened"])          # browser ran; _login_headless escalated


class TestScheduledReauthWeekendGate(TestScheduledReauth):
    """Scheduled callers pass weekend_mint=False, so on an ET weekend they only renew. Manual
    callers keep the default and can still log in on a weekend."""

    def _run_gate(self, *, weekend, weekend_mint, alive):
        patches = self._patches(alive=alive, trust="trusted")
        for p in patches.values():
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches.values()])
        pw = mock.patch.object(etrade, "_et_is_weekend", return_value=weekend)
        pw.start()
        self.addCleanup(pw.stop)
        tokens = {"issued_date_et": etrade._et_today()}
        lh = mock.patch.object(etrade, "_login_headless", return_value=tokens)
        m_lh = lh.start()
        self.addCleanup(lh.stop)
        return etrade.scheduled_reauth("production", weekend_mint=weekend_mint), m_lh

    def test_weekend_gate_matrix(self):
        alive = {"issued_date_et": etrade._et_today()}
        cases = [
            # weekend, weekend_mint, live token, expected reason, browser opened
            (True,  False, None,  "weekend",  False),  # scheduled, weekend, dead token: no login
            (True,  False, alive, "renewed",  False),  # a live token is still renewed on a weekend
            (False, False, None,  "reauthed", True),   # scheduled, weekday: logs in as before
            (True,  True,  None,  "reauthed", True),   # manual (default) can log in on a weekend
        ]
        for weekend, weekend_mint, live, reason, browser in cases:
            with self.subTest(weekend=weekend, weekend_mint=weekend_mint, live=bool(live)):
                res, m_lh = self._run_gate(weekend=weekend, weekend_mint=weekend_mint, alive=live)
                self.assertEqual(res["reason"], reason)
                self.assertEqual(res["browser_opened"], browser)
                self.assertEqual(m_lh.called, browser)
                self.doCleanups()


class TestEtIsWeekend(unittest.TestCase):
    """The weekend check uses the ET calendar, not the machine's local date."""

    def test_et_calendar_boundaries(self):
        et = etrade._ET
        cases = [
            ((2026, 10, 2, 23, 30), False),   # Fri 23:30 ET (already Sat in UTC)
            ((2026, 10, 3, 0, 30),  True),    # Sat 00:30 ET (= Fri 21:30 PT, the preflight run)
            ((2026, 10, 4, 23, 59), True),    # Sun 23:59 ET
            ((2026, 10, 5, 0, 0),   False),   # Mon 00:00 ET (= Sun 21:00 PT): Monday's token
        ]
        for (y, mo, d, h, mi), expected in cases:
            with self.subTest(et=f"{y}-{mo:02d}-{d:02d} {h:02d}:{mi:02d}"):
                epoch = _et_epoch(et, y, mo, d, h, mi)
                with mock.patch.object(etrade.time, "time", return_value=epoch):
                    self.assertEqual(etrade._et_is_weekend(), expected)


def _et_epoch(et, y, mo, d, h, mi):
    naive = datetime.datetime(y, mo, d, h, mi)
    aware = et.localize(naive) if hasattr(et, "localize") else naive.replace(tzinfo=et)
    return aware.timestamp()


class TestScheduledCliWeekendExit(unittest.TestCase):
    """`server.py etrade-reauth --scheduled` passes weekend_mint=False and exits 0 for a weekend
    skip, so the Task Scheduler run doesn't show as failed on weekends."""

    def test_scheduled_flag_and_exit_codes(self):
        cases = [
            # scheduled, result from the door,                    weekend_mint passed, exit code
            (True,  {"ok": False, "reason": "weekend"},            False, 0),
            (True,  {"ok": False, "reason": "failed"},             False, 1),
            (True,  {"ok": True,  "reason": "reauthed"},           False, 0),
            (False, {"ok": True,  "reason": "reauthed"},           True,  0),
        ]
        for scheduled, result, weekend_mint, code in cases:
            with self.subTest(scheduled=scheduled, reason=result["reason"]),                  mock.patch.object(server.etrade, "scheduled_reauth", return_value=result) as door,                  mock.patch.object(server.notify, "send_reauth_alert"):
                with self.assertRaises(SystemExit) as cm:
                    server.cmd_etrade_reauth(scheduled=scheduled)
                self.assertEqual(cm.exception.code, code)
                door.assert_called_once_with("production", weekend_mint=weekend_mint)


# ---------------------------------------------------------------------------
# 4. The throttled re-auth alert + desktop push
# ---------------------------------------------------------------------------

class TestReauthAlert(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._marker = Path(self._tmp.name) / "etrade_reauth_alert_sent.txt"
        mp = mock.patch.object(notify, "_reauth_alert_marker", return_value=self._marker)
        mp.start()
        self.addCleanup(mp.stop)
        # Never spawn a real toast in tests; email is the throttle's source of truth.
        pp = mock.patch.object(notify, "send_desktop_push", return_value=True)
        pp.start()
        self.addCleanup(pp.stop)

    def test_fires_once_per_episode_then_throttles(self):
        with mock.patch.object(notify, "send_email") as m_email:
            first = notify.send_reauth_alert("production", "sms_required")
            second = notify.send_reauth_alert("production", "sms_required")
        self.assertTrue(first)
        self.assertFalse(second)                # same episode → throttled, no second email
        m_email.assert_called_once()
        self.assertTrue(self._marker.exists())

    def test_clear_rearms_the_alert(self):
        with mock.patch.object(notify, "send_email") as m_email:
            notify.send_reauth_alert("production", "sms_required")   # fires, marks episode
            notify.clear_reauth_alert("production")                  # next mint clears throttle
            self.assertFalse(self._marker.exists())
            again = notify.send_reauth_alert("production", "sms_required")
        self.assertTrue(again)                  # a fresh episode re-alerts
        self.assertEqual(m_email.call_count, 2)

    def test_marker_not_written_when_email_fails(self):
        # Email is the reliable channel — if it fails, the episode must NOT be marked sent, so
        # the next run retries the alert rather than going silent. (redirect_stdout swallows the
        # code's own "email dispatch failed" warning so it doesn't clutter the suite output.)
        with mock.patch.object(notify, "send_email", side_effect=RuntimeError("smtp down")), \
             contextlib.redirect_stdout(io.StringIO()):
            out = notify.send_reauth_alert("production", "sms_required")
        self.assertFalse(out)
        self.assertFalse(self._marker.exists())


class TestDesktopPush(unittest.TestCase):
    def test_never_raises_when_powershell_absent_or_errors(self):
        # send_desktop_push is best-effort: a missing/failing PowerShell must yield False, never
        # an exception that could take down the alert path.
        with mock.patch.object(notify.subprocess, "run", side_effect=FileNotFoundError):
            self.assertIs(notify.send_desktop_push("t", "m"), False)
        with mock.patch.object(notify.subprocess, "run", side_effect=Exception("boom")):
            self.assertIs(notify.send_desktop_push("t", "m"), False)

    def test_returns_bool_without_raising(self):
        # Whatever the platform, the call returns a bool and does not raise. Mock the subprocess
        # seam so the test never actually spawns PowerShell / pops a real toast during a run.
        with mock.patch.object(notify.subprocess, "run", return_value=mock.MagicMock(returncode=0)):
            self.assertIsInstance(notify.send_desktop_push("title", "msg"), bool)


if __name__ == "__main__":
    unittest.main()
