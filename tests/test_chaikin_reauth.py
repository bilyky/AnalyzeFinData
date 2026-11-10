"""
Unit tests for scripts/monitoring/chaikin_reauth.py — the JWT-expiry decoder that
drives the proactive weekly re-auth's runway gate. No network, no browser.

  python -m unittest discover -s tests -v
"""
import base64
import datetime
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "monitoring"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import chaikin_reauth as cr


def _make_jwt(payload: dict) -> str:
    """Build a syntactically valid (unsigned) JWT with the given payload claims."""
    def b64(obj):
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    return f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64(payload)}.sig"


class TestDecodeExp(unittest.TestCase):
    def test_valid_exp_returns_utc_datetime(self):
        exp_ts = 1_900_000_000  # fixed epoch seconds
        got = cr._decode_exp(_make_jwt({"exp": exp_ts}))
        self.assertIsNotNone(got)
        self.assertEqual(got, datetime.datetime.fromtimestamp(exp_ts, tz=datetime.timezone.utc))
        self.assertEqual(got.tzinfo, datetime.timezone.utc)  # tz-aware, never naive

    def test_exp_padding_restored(self):
        # A payload whose base64url length is not a multiple of 4 must still decode
        # (the helper restores stripped '=' padding). Assert it round-trips.
        exp_ts = 1_888_777_666
        token = _make_jwt({"exp": exp_ts, "iat": exp_ts - 604800})
        got = cr._decode_exp(token)
        self.assertEqual(got, datetime.datetime.fromtimestamp(exp_ts, tz=datetime.timezone.utc))

    def test_missing_exp_claim_returns_none(self):
        self.assertIsNone(cr._decode_exp(_make_jwt({"iat": 123})))

    def test_garbage_token_returns_none(self):
        for bad in ("", "not-a-jwt", "only.two", "a.!!!notbase64!!!.c"):
            self.assertIsNone(cr._decode_exp(bad), f"expected None for {bad!r}")

    def test_non_integer_exp_returns_none(self):
        # exp that can't be int()'d must be swallowed, not raised.
        self.assertIsNone(cr._decode_exp(_make_jwt({"exp": "soon"})))


class TestSessionFromStore(unittest.TestCase):
    def test_maps_members_camelcase(self):
        store = {"sessionToken": "tok", "jsessionId": "SID", "sessionKey": "SK",
                 "email": "u@e.com"}
        self.assertEqual(
            cr._session_from_store(store),
            {"jwttoken": "tok", "jsessionid": "SID", "uuid": "u@e.com"},
        )

    def test_falls_back_to_sessionKey_then_sessionId(self):
        self.assertEqual(
            cr._session_from_store({"sessionToken": "t", "sessionKey": "SK"})["jsessionid"], "SK")
        self.assertEqual(
            cr._session_from_store({"sessionToken": "t", "sessionId": "SI"})["jsessionid"], "SI")

    def test_missing_token_returns_empty(self):
        self.assertEqual(cr._session_from_store({"jsessionId": "x"}), {})
        self.assertEqual(cr._session_from_store({}), {})
        self.assertEqual(cr._session_from_store(None), {})


class TestEmailThrottle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig = cr._NOTIFY_STATE
        cr._NOTIFY_STATE = os.path.join(self.tmp, "notify.json")

    def tearDown(self):
        cr._NOTIFY_STATE = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_read_is_pure_then_stamp_throttles(self):
        # _email_throttled is a pure read: repeated checks stay False until a caller stamps.
        self.assertFalse(cr._email_throttled("k", 6.0))
        self.assertFalse(cr._email_throttled("k", 6.0))  # still False — read has no side-effect
        cr._stamp_email_sent("k")                        # only an explicit stamp opens the window
        self.assertTrue(cr._email_throttled("k", 6.0))   # now throttled

    def test_aged_stamp_allows_again(self):
        old = datetime.datetime.now(tz=datetime.timezone.utc).timestamp() - 7 * 3600
        with open(cr._NOTIFY_STATE, "w", encoding="utf-8") as fh:
            json.dump({"k": old}, fh)
        self.assertFalse(cr._email_throttled("k", 6.0))  # 7h older than the 6h window

    def test_kinds_are_independent(self):
        cr._stamp_email_sent("a")
        self.assertTrue(cr._email_throttled("a", 6.0))
        self.assertFalse(cr._email_throttled("b", 6.0))  # different kind is not throttled


class TestCdpDispatch(unittest.TestCase):
    def test_cdp_flag_dispatches(self):
        orig_argv = sys.argv
        sys.argv = ["chaikin_reauth.py", "--cdp"]
        try:
            with mock.patch.object(cr, "_run_cdp_reauth", return_value=0) as m:
                rc = cr.main()
        finally:
            sys.argv = orig_argv
        self.assertEqual(rc, 0)
        m.assert_called_once()

    def test_check_does_not_dispatch_cdp(self):
        orig_argv = sys.argv
        sys.argv = ["chaikin_reauth.py", "--check"]
        try:
            with mock.patch.object(cr, "_run_cdp_reauth") as m, \
                 mock.patch.object(cr.pg, "_load_session_from_file", return_value={}):
                cr.main()
        finally:
            sys.argv = orig_argv
        m.assert_not_called()


class TestNotifyOnFailure(unittest.TestCase):
    def test_headed_failure_notifies_and_returns_1(self):
        calls = []
        orig_argv = sys.argv
        sys.argv = ["chaikin_reauth.py", "--force"]
        try:
            with mock.patch.object(cr.pg, "_load_session_from_file", return_value={}), \
                 mock.patch.object(cr, "_backup_session", lambda: None), \
                 mock.patch.object(cr.pg, "_login_via_browser",
                                   side_effect=RuntimeError("turnstile 600010")), \
                 mock.patch.object(cr, "_notify_manual_reauth",
                                   side_effect=lambda reason: calls.append(reason)):
                rc = cr.main()
        finally:
            sys.argv = orig_argv
        self.assertEqual(rc, 1)
        self.assertEqual(len(calls), 1)


class TestNotifyDays(unittest.TestCase):
    """Runway-watch (--notify-days): browser-free, throttled daily nudge before expiry."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig_state = cr._NOTIFY_STATE
        cr._NOTIFY_STATE = os.path.join(self.tmp, "notify.json")

    def tearDown(self):
        cr._NOTIFY_STATE = self._orig_state
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, days_runway, threshold, sent):
        exp = (datetime.datetime.now(tz=datetime.timezone.utc)
               + datetime.timedelta(days=days_runway))
        session = {"jwttoken": _make_jwt({"exp": int(exp.timestamp())}),
                   "jsessionid": "x", "uuid": "u@e.com"}
        orig_argv = sys.argv
        sys.argv = ["chaikin_reauth.py", "--notify-days", str(threshold)]
        try:
            with mock.patch.object(cr.pg, "_load_session_from_file", return_value=session), \
                 mock.patch.object(cr.pg, "_probe_session", return_value="valid"), \
                 mock.patch.object(cr, "send_email",
                                   side_effect=lambda **kw: sent.append(kw)), \
                 mock.patch.object(cr.pg, "_login_via_browser",
                                   side_effect=AssertionError("must NOT launch a browser")):
                rc = cr.main()
        finally:
            sys.argv = orig_argv
        return rc

    def test_emails_when_below_threshold(self):
        sent = []
        rc = self._run(days_runway=1.0, threshold=2.5, sent=sent)
        self.assertEqual(rc, 0)
        self.assertEqual(len(sent), 1)

    def test_no_email_when_above_threshold(self):
        sent = []
        rc = self._run(days_runway=5.0, threshold=2.5, sent=sent)
        self.assertEqual(rc, 0)
        self.assertEqual(sent, [])

    def test_throttled_across_two_runs(self):
        sent = []
        self._run(days_runway=1.0, threshold=2.5, sent=sent)
        self._run(days_runway=1.0, threshold=2.5, sent=sent)  # second run within 20h
        self.assertEqual(len(sent), 1)  # only one email despite two below-threshold runs

    def test_send_failure_does_not_burn_throttle(self):
        # A transient SMTP failure must NOT stamp the throttle — the reminder is retried next run.
        exp = (datetime.datetime.now(tz=datetime.timezone.utc)
               + datetime.timedelta(days=1.0))
        session = {"jwttoken": _make_jwt({"exp": int(exp.timestamp())}),
                   "jsessionid": "x", "uuid": "u@e.com"}
        orig_argv = sys.argv
        sys.argv = ["chaikin_reauth.py", "--notify-days", "2.5"]
        try:
            # First run: send raises (transient SMTP failure) — window must stay open.
            with mock.patch.object(cr.pg, "_load_session_from_file", return_value=session), \
                 mock.patch.object(cr.pg, "_probe_session", return_value="valid"), \
                 mock.patch.object(cr, "send_email",
                                   side_effect=OSError("smtp down")), \
                 mock.patch.object(cr.pg, "_login_via_browser",
                                   side_effect=AssertionError("must NOT launch a browser")):
                self.assertEqual(cr.main(), 0)
            self.assertFalse(cr._email_throttled("runway_low", 20.0),
                             "a failed send must not stamp the throttle")
            # Second run: send succeeds — the reminder that the failure would have burned goes out.
            sent = []
            self._run(days_runway=1.0, threshold=2.5, sent=sent)
            self.assertEqual(len(sent), 1)
        finally:
            sys.argv = orig_argv


_PT = cr.pytz.timezone("America/Los_Angeles")
_UTC = datetime.timezone.utc


def _pt(y, m, d, hh=8, mm=0):
    return _PT.localize(datetime.datetime(y, m, d, hh, mm))


class TestWeekdayRefreshDue(unittest.TestCase):
    """The 7-day token kept expiring on Sunday evening; the Sunday reauth failed with no working day
    left (PROD 11-08). Refresh on a weekday when the token would not outlive the next weekday run
    plus a day; at the weekend only when it dies before Monday's run (PROD review 11-09)."""

    EXP_1109 = datetime.datetime(2026, 11, 9, 1, 44, 50, tzinfo=_UTC)     # Sun 11-08 17:44 PT
    EXP_1116 = datetime.datetime(2026, 11, 16, 14, 35, 43, tzinfo=_UTC)   # Mon 11-16 06:35 PT

    def test_friday_refreshes_a_token_that_dies_over_the_weekend(self):
        self.assertTrue(cr.weekday_refresh_due(self.EXP_1109, _pt(2026, 11, 6)))
        self.assertTrue(cr.weekday_refresh_due(self.EXP_1116, _pt(2026, 11, 13)))

    def test_thursday_waits_when_friday_still_has_a_day(self):
        self.assertFalse(cr.weekday_refresh_due(self.EXP_1109, _pt(2026, 11, 5)))
        self.assertFalse(cr.weekday_refresh_due(self.EXP_1116, _pt(2026, 11, 12)))

    def test_midweek_with_plenty_of_runway_waits(self):
        self.assertFalse(cr.weekday_refresh_due(self.EXP_1116, _pt(2026, 11, 9)))

    def test_weekend_only_as_a_last_resort(self):
        self.assertTrue(cr.weekday_refresh_due(self.EXP_1116, _pt(2026, 11, 14)))     # dies Mon 06:35
        later = datetime.datetime(2026, 11, 17, 18, 0, tzinfo=_UTC)                    # Tue 10:00 PT
        self.assertFalse(cr.weekday_refresh_due(later, _pt(2026, 11, 14)))

    def test_expired_or_unknown_is_always_due(self):
        self.assertTrue(cr.weekday_refresh_due(self.EXP_1109, _pt(2026, 11, 9)))
        self.assertTrue(cr.weekday_refresh_due(None, _pt(2026, 11, 10)))


class TestWeekdayAwareMain(unittest.TestCase):
    def _run(self, now, exp):
        orig_argv = sys.argv
        sys.argv = ["chaikin_reauth.py", "--weekday-aware"]
        try:
            with mock.patch.object(cr.pg, "_load_session_from_file", return_value={"jwttoken": "x"}), \
                 mock.patch.object(cr, "_decode_exp", return_value=exp), \
                 mock.patch.object(cr.pg, "_probe_session", return_value="valid"), \
                 mock.patch.object(cr, "_now_local", return_value=now), \
                 mock.patch.object(cr, "_backup_session", lambda: None), \
                 mock.patch.object(cr.pg, "_login_via_browser", return_value={}) as login, \
                 mock.patch.object(cr, "_notify_manual_reauth"):
                cr.main()
        finally:
            sys.argv = orig_argv
        return login

    def test_not_due_opens_no_browser(self):
        self._run(_pt(2026, 11, 12), TestWeekdayRefreshDue.EXP_1116).assert_not_called()

    def test_due_runs_the_headed_login(self):
        self._run(_pt(2026, 11, 13), TestWeekdayRefreshDue.EXP_1116).assert_called_once_with(headless=False)


class TestReminderNamesTheDeadline(unittest.TestCase):
    def test_runway_watch_subject_names_the_expiry(self):
        orig_argv = sys.argv
        sys.argv = ["chaikin_reauth.py", "--notify-days", "2.5"]
        exp = TestWeekdayRefreshDue.EXP_1116
        now = datetime.datetime(2026, 11, 14, 15, 0, tzinfo=_UTC)
        try:
            with mock.patch.object(cr.pg, "_load_session_from_file", return_value={"jwttoken": "x"}), \
                 mock.patch.object(cr, "_decode_exp", return_value=exp), \
                 mock.patch.object(cr.pg, "_probe_session", return_value="valid"), \
                 mock.patch.object(cr, "_now_utc", return_value=now), \
                 mock.patch.object(cr, "_email_throttled", return_value=False), \
                 mock.patch.object(cr, "_stamp_email_sent"), \
                 mock.patch.object(cr, "send_email") as send:
                cr.main()
        finally:
            sys.argv = orig_argv
        self.assertIn("Mon 11-16 06:35 PT", send.call_args.kwargs["subject"])


if __name__ == "__main__":
    unittest.main()
