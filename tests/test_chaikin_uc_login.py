"""
Unit tests for scripts/diagnostics/chaikin_uc_login.py — the EXPERIMENTAL undetected-
chromedriver research probe. No network, no browser. These cover only the isolated,
browser-free branches (fingerprinting, JWT decode, and the dependency-missing SKIP path);
the actual UC login is a manual/E2E path that needs a real desktop browser.

  python -m unittest discover -s tests -v
"""
import base64
import datetime
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "diagnostics"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import chaikin_uc_login as uc_probe


def _make_jwt(payload: dict) -> str:
    """Build a syntactically valid (unsigned) JWT with the given payload claims."""
    def b64(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()
    return f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64(payload)}.sig"


class TestFingerprint(unittest.TestCase):
    def test_never_leaks_value(self):
        fp = uc_probe._fp("super-secret-token-value")
        self.assertNotIn("secret", fp)
        self.assertTrue(fp.startswith("len=24 sha="))

    def test_none_and_empty(self):
        self.assertEqual(uc_probe._fp(None), "None")
        self.assertEqual(uc_probe._fp(""), "len=0 (empty)")

    def test_stable_across_calls(self):
        self.assertEqual(uc_probe._fp("abc"), uc_probe._fp("abc"))


class TestDecodeExp(unittest.TestCase):
    def test_valid_exp(self):
        exp_ts = 1_900_000_000
        got = uc_probe._decode_exp(_make_jwt({"exp": exp_ts}))
        self.assertEqual(got, datetime.datetime.fromtimestamp(exp_ts, tz=datetime.timezone.utc))

    def test_garbage_returns_none(self):
        for bad in ("", "not-a-jwt", "only.two", "a.!!!.c"):
            self.assertIsNone(uc_probe._decode_exp(bad))

    def test_missing_exp_returns_none(self):
        self.assertIsNone(uc_probe._decode_exp(_make_jwt({"iat": 1})))


class TestSkipWhenUcMissing(unittest.TestCase):
    def test_main_returns_3_and_touches_nothing(self):
        orig_argv = sys.argv
        sys.argv = ["chaikin_uc_login.py"]
        try:
            # Simulate undetected_chromedriver not installed, and prove no browser is built.
            with mock.patch.object(uc_probe, "uc", None), \
                 mock.patch.object(uc_probe, "_build_driver",
                                   side_effect=AssertionError("must NOT build a driver")), \
                 mock.patch.object(uc_probe, "_write_probe",
                                   side_effect=AssertionError("must NOT write a probe file")):
                rc = uc_probe.main()
        finally:
            sys.argv = orig_argv
        self.assertEqual(rc, 3)


if __name__ == "__main__":
    unittest.main()
