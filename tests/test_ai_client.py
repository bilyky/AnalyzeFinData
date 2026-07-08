"""
Tests for ai_client.py — provider selection + per-type dispatch.
All network calls are mocked; no real API hits.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config as config_module
import ai_client


def _set_providers(primary, providers):
    config_module.CFG.ai_primary = primary
    config_module.CFG.ai_providers = providers


GH = {"enabled": True, "type": "openai_compatible", "model": "gpt-4o-mini",
      "endpoint": "https://example/chat", "api_key_source": "env:TESTKEY"}
CLAUDE = {"enabled": True, "type": "anthropic", "model": "claude-opus-4-8",
          "api_key_source": "env:ANTHROPIC_TESTKEY"}


class _Base(unittest.TestCase):
    def setUp(self):
        self._p = (config_module.CFG.ai_primary, config_module.CFG.ai_providers)
        self._env = os.environ.copy()

    def tearDown(self):
        config_module.CFG.ai_primary, config_module.CFG.ai_providers = self._p
        os.environ.clear(); os.environ.update(self._env)


class TestProviderSelection(_Base):
    def test_enabled_providers_filters_keyless(self):
        _set_providers("github_gpt", {"github_gpt": GH, "claude": CLAUDE})
        os.environ["TESTKEY"] = "k"            # github has a key
        os.environ.pop("ANTHROPIC_TESTKEY", None)  # claude does not
        self.assertEqual(ai_client.enabled_providers(), ["github_gpt"])

    def test_enabled_providers_skips_disabled(self):
        _set_providers("github_gpt", {"github_gpt": {**GH, "enabled": False}})
        os.environ["TESTKEY"] = "k"
        self.assertEqual(ai_client.enabled_providers(), [])

    def test_primary_falls_back_to_first_enabled(self):
        _set_providers("github_gpt", {"github_gpt": {**GH, "enabled": False},
                                      "claude": CLAUDE})
        os.environ["ANTHROPIC_TESTKEY"] = "k"
        self.assertEqual(ai_client.primary(), "claude")

    def test_primary_none_when_nothing_usable(self):
        _set_providers("github_gpt", {"github_gpt": {**GH, "enabled": False}})
        self.assertIsNone(ai_client.primary())

    def test_gemini_cli_needs_no_key(self):
        _set_providers("gem", {"gem": {"enabled": True, "type": "gemini_cli", "model": "g"}})
        self.assertEqual(ai_client.enabled_providers(), ["gem"])


class TestKeyResolution(_Base):
    def test_env_key(self):
        os.environ["TESTKEY"] = "secret"
        self.assertEqual(ai_client._resolve_key("env:TESTKEY"), "secret")

    def test_literal_key(self):
        self.assertEqual(ai_client._resolve_key("literal-abc"), "literal-abc")

    def test_missing_returns_empty(self):
        os.environ.pop("NOPE", None)
        self.assertEqual(ai_client._resolve_key("env:NOPE"), "")


class TestEvaluateDispatch(_Base):
    def test_openai_compatible_call(self):
        _set_providers("gh", {"gh": GH})
        os.environ["TESTKEY"] = "k"
        fake = mock.Mock(status_code=200)
        fake.json.return_value = {"choices": [{"message": {"content": " hi "}}]}
        fake.raise_for_status = mock.Mock()
        with mock.patch("ai_client.requests.post", return_value=fake) as post:
            out = ai_client.evaluate("sys", "usr")
        self.assertEqual(out, "hi")
        # temperature IS sent for openai-compatible
        self.assertIn("temperature", post.call_args.kwargs["json"])

    def test_anthropic_call_omits_temperature(self):
        _set_providers("c", {"c": CLAUDE})
        os.environ["ANTHROPIC_TESTKEY"] = "k"
        fake = mock.Mock(status_code=200)
        fake.json.return_value = {"content": [{"type": "text", "text": "verdict"}]}
        fake.raise_for_status = mock.Mock()
        with mock.patch("ai_client.requests.post", return_value=fake) as post:
            out = ai_client.evaluate("sys", "usr")
        self.assertEqual(out, "verdict")
        body = post.call_args.kwargs["json"]
        self.assertNotIn("temperature", body)      # Opus 4.7+ rejects it
        self.assertEqual(body["model"], "claude-opus-4-8")

    def test_no_usable_provider_returns_empty(self):
        _set_providers("gh", {"gh": {**GH, "enabled": False}})
        self.assertEqual(ai_client.evaluate("s", "u"), "")

    def test_http_error_returns_empty_not_raise(self):
        _set_providers("gh", {"gh": GH})
        os.environ["TESTKEY"] = "k"
        with mock.patch("ai_client.requests.post", side_effect=Exception("boom")):
            self.assertEqual(ai_client.evaluate("s", "u"), "")

    def test_explicit_provider_arg_overrides_primary(self):
        _set_providers("gh", {"gh": GH, "c": CLAUDE})
        os.environ["TESTKEY"] = "k"; os.environ["ANTHROPIC_TESTKEY"] = "k"
        fake = mock.Mock(status_code=200)
        fake.json.return_value = {"content": [{"type": "text", "text": "claude-said"}]}
        fake.raise_for_status = mock.Mock()
        with mock.patch("ai_client.requests.post", return_value=fake):
            out = ai_client.evaluate("s", "u", provider="c")
        self.assertEqual(out, "claude-said")


if __name__ == "__main__":
    unittest.main()
