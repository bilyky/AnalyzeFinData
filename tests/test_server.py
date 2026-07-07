"""
Tests for server.py auth primitives (token signing + credential check).
Pure-function tests — no HTTP server or httpx required.
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import server


class TestTokenRoundTrip(unittest.TestCase):
    SECRET = "unit_test_secret_key"

    def test_valid_token_returns_user(self):
        t = server.make_token("yuriy", self.SECRET)
        self.assertEqual(server.verify_token(t, self.SECRET), "yuriy")

    def test_wrong_secret_rejected(self):
        t = server.make_token("yuriy", self.SECRET)
        self.assertIsNone(server.verify_token(t, "different_secret"))

    def test_tampered_signature_rejected(self):
        t = server.make_token("yuriy", self.SECRET)
        tampered = t[:-4] + ("AAAA" if not t.endswith("AAAA") else "BBBB")
        self.assertIsNone(server.verify_token(tampered, self.SECRET))

    def test_tampered_payload_rejected(self):
        # Swap the payload (different user) but keep the old signature
        good = server.make_token("alice", self.SECRET)
        other = server.make_token("attacker", self.SECRET)
        forged = other.split(".")[0] + "." + good.split(".")[1]
        self.assertIsNone(server.verify_token(forged, self.SECRET))

    def test_expired_token_rejected(self):
        t = server.make_token("yuriy", self.SECRET, ttl=-10)
        self.assertIsNone(server.verify_token(t, self.SECRET))

    def test_empty_token_rejected(self):
        self.assertIsNone(server.verify_token("", self.SECRET))

    def test_empty_secret_rejected(self):
        t = server.make_token("yuriy", self.SECRET)
        self.assertIsNone(server.verify_token(t, ""))

    def test_garbage_token_rejected(self):
        self.assertIsNone(server.verify_token("not-a-real-token", self.SECRET))


class TestCredentialCheck(unittest.TestCase):
    ADMINS = [{"user": "yuriy", "pass": "s3cret"}, {"user": "ops", "pass": "hunter2"}]

    def test_valid_first_admin(self):
        self.assertTrue(server.check_credentials("yuriy", "s3cret", self.ADMINS))

    def test_valid_second_admin(self):
        self.assertTrue(server.check_credentials("ops", "hunter2", self.ADMINS))

    def test_wrong_password(self):
        self.assertFalse(server.check_credentials("yuriy", "nope", self.ADMINS))

    def test_wrong_user(self):
        self.assertFalse(server.check_credentials("ghost", "s3cret", self.ADMINS))

    def test_empty_admin_list(self):
        self.assertFalse(server.check_credentials("yuriy", "s3cret", []))

    def test_cross_user_password_rejected(self):
        # yuriy's username with ops's password must not authenticate
        self.assertFalse(server.check_credentials("yuriy", "hunter2", self.ADMINS))


if __name__ == "__main__":
    unittest.main()
