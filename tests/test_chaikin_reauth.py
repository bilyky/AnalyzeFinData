"""
Unit tests for scripts/monitoring/chaikin_reauth.py — the JWT-expiry decoder that
drives the proactive weekly re-auth's runway gate. No network, no browser.

  python -m unittest discover -s tests -v
"""
import base64
import datetime
import json
import os
import sys
import unittest

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


if __name__ == "__main__":
    unittest.main()
