"""
Preflight AI-provider check.

PROD 2026-09-24..10-05: the primary provider's endpoint host was misspelled
(`openrpouter.ai`) and every advisory AI call failed for 12 days with nothing in the
preflight to show it. check_ai_providers() catches that class of config error without
spending a model request: each tier needs at least one usable provider (enabled + key),
and every HTTP endpoint in use must be an https URL whose host resolves.

The AI is advisory, so the check is reported as WARN and never blocks the run
(the "advisory" kind is left out of the overall verdict). Sockets are mocked.
"""
import os
import socket
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "diagnostics"))
import preflight_validator as pf


def _p(**kw):
    base = {"type": "openai_compatible", "enabled": True, "model": "m",
            "endpoint": "https://openrouter.ai/api/v1/chat/completions", "api_key_source": "env:FAKE"}
    base.update(kw)
    return base


class _Cfg:
    def __init__(self, providers, tiers=None, primary=""):
        self.ai_providers = providers
        self.ai_tiers = tiers or {}
        self.ai_primary = primary


def _resolves_except(*bad_hosts):
    def fake(host, *a, **k):
        if host in bad_hosts:
            raise socket.gaierror(11001, "getaddrinfo failed")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.2.3.4", 443))]
    return fake


class TestCheckAIProviders(unittest.TestCase):

    def _run(self, cfg, bad_hosts=()):
        with mock.patch.object(pf.ai_client, "CFG", cfg), \
             mock.patch.object(pf.ai_client, "_resolve_key", return_value="K"), \
             mock.patch.object(pf.socket, "getaddrinfo", side_effect=_resolves_except(*bad_hosts)):
            return pf.check_ai_providers()

    def test_healthy_config_passes(self):
        ok, issues = self._run(_Cfg({"a": _p()}, tiers={"cheap": ["a"], "strong": ["a"]}))
        self.assertTrue(ok, issues)
        self.assertEqual(issues, [])

    def test_misspelled_host_is_reported(self):
        cfg = _Cfg({"a": _p(endpoint="https://openrpouter.ai/inference/chat/completions")},
                   tiers={"cheap": ["a"]})
        ok, issues = self._run(cfg, bad_hosts=("openrpouter.ai",))
        self.assertFalse(ok)
        self.assertTrue(any("openrpouter.ai" in i and "'a'" in i for i in issues), issues)

    def test_tier_with_no_usable_provider_is_reported(self):
        cfg = _Cfg({"a": _p(enabled=False), "b": _p()}, tiers={"cheap": ["a"], "strong": ["b"]})
        ok, issues = self._run(cfg)
        self.assertFalse(ok)
        self.assertTrue(any("cheap" in i for i in issues), issues)

    def test_non_https_endpoint_is_reported(self):
        cfg = _Cfg({"a": _p(endpoint="openrouter.ai/api/v1")}, tiers={"cheap": ["a"]})
        ok, issues = self._run(cfg)
        self.assertFalse(ok)
        self.assertTrue(any("https" in i for i in issues), issues)

    def test_no_providers_at_all_is_reported(self):
        ok, issues = self._run(_Cfg({}))
        self.assertFalse(ok)
        self.assertTrue(issues)

    def test_gemini_cli_needs_no_endpoint(self):
        cfg = _Cfg({"g": {"type": "gemini_cli", "enabled": True, "model": "x"}}, tiers={"cheap": ["g"]})
        ok, issues = self._run(cfg)
        self.assertTrue(ok, issues)


class TestAdvisoryChecksDoNotBlock(unittest.TestCase):

    def test_failed_advisory_check_does_not_fail_the_run(self):
        checks = [("Gmail IMAP", True, "conn"), ("AI Providers", False, "advisory")]
        self.assertTrue(pf.overall_ok(checks))

    def test_failed_connection_check_still_fails_the_run(self):
        checks = [("Gmail IMAP", False, "conn"), ("AI Providers", True, "advisory")]
        self.assertFalse(pf.overall_ok(checks))

    def test_advisory_failure_is_labeled_warn_not_pass(self):
        self.assertEqual(pf.status_word(False, "advisory"), "WARN")
        self.assertEqual(pf.status_word(True, "advisory"), "PASS")
        self.assertEqual(pf.status_word(False, "conn"), "FAIL")
        self.assertEqual(pf.status_word(False, "lock"), "LOCKED")
        self.assertEqual(pf.status_word("WAIVED", "conn"), "WAIVED")


if __name__ == "__main__":
    unittest.main()
