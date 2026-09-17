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

    def test_first_stamps_then_throttles(self):
        self.assertFalse(cr._email_throttled("k", 6.0))  # first call stamps 'now'
        self.assertTrue(cr._email_throttled("k", 6.0))   # immediate re-check is throttled

    def test_aged_stamp_allows_again(self):
        old = datetime.datetime.now(tz=datetime.timezone.utc).timestamp() - 7 * 3600
        with open(cr._NOTIFY_STATE, "w", encoding="utf-8") as fh:
            json.dump({"k": old}, fh)
        self.assertFalse(cr._email_throttled("k", 6.0))  # 7h older than the 6h window

    def test_kinds_are_independent(self):
        self.assertFalse(cr._email_throttled("a", 6.0))
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


if __name__ == "__main__":
    unittest.main()
