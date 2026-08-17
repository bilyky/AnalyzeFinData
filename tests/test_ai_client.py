"""
Unit tests for the configurable multi-provider ai_client.

These lock the transport contract that evaluate() and chat() share:
  - openai_compatible sends [system, user] (evaluate) / [system]+history (chat)
    and passes max_tokens + temperature;
  - anthropic sends `system` out-of-band and DELIBERATELY omits temperature
    (recent Claude models reject it) — the red/green counterpart to openai;
  - gemini_cli feeds the payload on stdin and the directive via -p.
All network / subprocess is mocked; nothing here hits a real provider.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import aether.ai_client as ai_client

_GPT    = {"type": "openai_compatible", "enabled": True,
           "endpoint": "https://example/api", "model": "gpt-x", "api_key_source": "env:FAKE"}
_CLAUDE = {"type": "anthropic", "enabled": True, "model": "claude-x", "api_key_source": "env:FAKE"}
_GEMINI = {"type": "gemini_cli", "enabled": True, "model": "gemini-x"}
_BAD    = {"type": "quantum-oracle", "enabled": True, "api_key_source": "env:FAKE"}


def _resp(json_data):
    r = mock.MagicMock()
    r.json.return_value = json_data
    r.raise_for_status.return_value = None
    return r


class TestAIClientTransport(unittest.TestCase):

    def _providers(self, mapping):
        return mock.patch.object(ai_client, "_providers", return_value=mapping)

    @mock.patch.object(ai_client, "_resolve_key", return_value="KEY")
    @mock.patch("aether.ai_client.requests.post")
    def test_evaluate_openai_sends_system_user_and_params(self, m_post, _key):
        m_post.return_value = _resp({"choices": [{"message": {"content": "  hi  "}}]})
        with self._providers({"gpt": _GPT}):
            out = ai_client.evaluate("SYS", "USR", provider="gpt", max_tokens=50, temperature=0.1)
        self.assertEqual(out, "hi")  # parsed + stripped
        body = m_post.call_args.kwargs["json"]
        self.assertEqual(body["messages"],
                         [{"role": "system", "content": "SYS"}, {"role": "user", "content": "USR"}])
        self.assertEqual(body["max_tokens"], 50)
        self.assertEqual(body["temperature"], 0.1)
        self.assertEqual(m_post.call_args.kwargs["headers"]["Authorization"], "Bearer KEY")

    @mock.patch.object(ai_client, "_resolve_key", return_value="KEY")
    @mock.patch("aether.ai_client.requests.post")
    def test_chat_openai_prepends_system_to_history(self, m_post, _key):
        m_post.return_value = _resp({"choices": [{"message": {"content": "ok"}}]})
        history = [{"role": "user", "content": "a"},
                   {"role": "assistant", "content": "b"},
                   {"role": "user", "content": "c"}]
        with self._providers({"gpt": _GPT}):
            ai_client.chat(history, system="SYS", provider="gpt")
        body = m_post.call_args.kwargs["json"]
        self.assertEqual(body["messages"], [{"role": "system", "content": "SYS"}] + history)

    @mock.patch.object(ai_client, "_resolve_key", return_value="KEY")
    @mock.patch("aether.ai_client.requests.post")
    def test_anthropic_omits_temperature_and_hoists_system(self, m_post, _key):
        # The green counterpart to the openai test: same call, DIFFERENT wire shape.
        m_post.return_value = _resp({"content": [{"type": "text", "text": "done"}]})
        with self._providers({"claude": _CLAUDE}):
            out = ai_client.evaluate("SYS", "USR", provider="claude", max_tokens=80, temperature=0.9)
        self.assertEqual(out, "done")
        body = m_post.call_args.kwargs["json"]
        self.assertNotIn("temperature", body)                 # must not leak temperature
        self.assertEqual(body["system"], "SYS")               # system is out-of-band
        self.assertEqual(body["messages"], [{"role": "user", "content": "USR"}])
        self.assertEqual(m_post.call_args.kwargs["headers"]["x-api-key"], "KEY")

    @mock.patch("aether.ai_client.shutil.which", return_value="/opt/bin/gemini")
    @mock.patch("aether.ai_client.subprocess.run")
    def test_gemini_feeds_stdin_payload_and_p_directive(self, m_run, _which):
        m_run.return_value = mock.MagicMock(returncode=0, stdout="  verdict  ", stderr="")
        with self._providers({"gem": _GEMINI}):
            out = ai_client.evaluate("SYS", "USR", provider="gem")
        self.assertEqual(out, "verdict")
        argv = m_run.call_args.args[0]
        self.assertEqual(argv[0], "/opt/bin/gemini")  # absolute path, no shell PATH lookup
        self.assertIn("-p", argv)
        self.assertEqual(m_run.call_args.kwargs["input"], "SYS\n\nUSR")  # payload on stdin

    @mock.patch("aether.ai_client.shutil.which", return_value="/opt/bin/gemini")
    @mock.patch("aether.ai_client.subprocess.run")
    def test_gemini_runs_locked_down_without_shell(self, m_run, _which):
        """Security contract: the CLI is pinned to no-tools/no-MCP and never runs via a shell."""
        m_run.return_value = mock.MagicMock(returncode=0, stdout="ok", stderr="")
        with self._providers({"gem": _GEMINI}):
            ai_client.evaluate("SYS", "USR", provider="gem")
        argv = m_run.call_args.args[0]
        kwargs = m_run.call_args.kwargs
        self.assertFalse(kwargs.get("shell", False))  # no cmd.exe string parsing of the directive
        self.assertIn("--allowed-tools", argv)
        self.assertEqual(argv[argv.index("--allowed-tools") + 1], "none")
        self.assertIn("--allowed-mcp-server-names", argv)
        self.assertEqual(argv[argv.index("--allowed-mcp-server-names") + 1], "none")

    @mock.patch("aether.ai_client.sys.platform", "win32")
    @mock.patch("aether.ai_client.shutil.which",
                return_value=r"C:\Users\x\AppData\Roaming\npm\gemini.cmd")
    @mock.patch("aether.ai_client.subprocess.run")
    def test_gemini_win32_cmd_wrapped_via_comspec_not_shell(self, m_run, _which):
        """On Windows a .cmd wrapper is routed through COMSPEC as a list, keeping shell=False."""
        m_run.return_value = mock.MagicMock(returncode=0, stdout="ok", stderr="")
        with self._providers({"gem": _GEMINI}), \
             mock.patch.dict("os.environ", {"COMSPEC": r"C:\Windows\System32\cmd.exe"}):
            ai_client.evaluate("SYS", "USR", provider="gem")
        argv = m_run.call_args.args[0]
        self.assertFalse(m_run.call_args.kwargs.get("shell", False))
        self.assertEqual(argv[0].lower(), r"c:\windows\system32\cmd.exe")
        self.assertEqual(argv[1], "/c")
        self.assertIn("--allowed-tools", argv)  # lockdown flags survive the wrapping

    @mock.patch("aether.ai_client.shutil.which", return_value="/opt/bin/gemini")
    @mock.patch("aether.ai_client.subprocess.run")
    def test_gemini_nonzero_exit_raises(self, m_run, _which):
        m_run.return_value = mock.MagicMock(returncode=2, stdout="", stderr="boom")
        with self._providers({"gem": _GEMINI}):
            with self.assertRaises(RuntimeError):
                ai_client.evaluate("SYS", "USR", provider="gem")

    @mock.patch("aether.ai_client.subprocess.run")
    @mock.patch("aether.ai_client.sys.platform", "win32")
    @mock.patch("aether.ai_client.os.path.exists", return_value=True)
    def test_gemini_path_injection_on_win32(self, m_exists, m_run):
        m_run.return_value = mock.MagicMock(returncode=0, stdout="OK", stderr="")
        with self._providers({"gem": _GEMINI}), \
             mock.patch.dict("os.environ", {"APPDATA": r"C:\Users\dummy"}):
            ai_client.evaluate("SYS", "USR", provider="gem")
        
        m_run.assert_called_once()
        run_env = m_run.call_args.kwargs.get("env")
        self.assertIsNotNone(run_env)
        self.assertIn(r"C:\Users\dummy\npm", run_env.get("PATH", ""))

    @mock.patch.object(ai_client, "_resolve_key", return_value="KEY")
    def test_unknown_provider_type_raises(self, _key):
        with self._providers({"bad": _BAD}):
            with self.assertRaises(NotImplementedError):
                ai_client.evaluate("s", "u", provider="bad")

    def test_disabled_provider_raises(self):
        with self._providers({"gpt": {**_GPT, "enabled": False}}):
            with self.assertRaises(RuntimeError):
                ai_client.evaluate("s", "u", provider="gpt")

    def test_missing_key_raises(self):
        with self._providers({"gpt": _GPT}), \
             mock.patch.object(ai_client, "_resolve_key", return_value=""):
            with self.assertRaises(RuntimeError):
                ai_client.evaluate("s", "u", provider="gpt")

    @mock.patch.object(ai_client, "_resolve_key", return_value="KEY")
    @mock.patch("aether.ai_client.requests.post")
    @mock.patch("aether.ai_client.subprocess.run")
    def test_evaluate_fallback_on_primary_failure(self, m_run, m_post, _key):
        # Primary provider 'gem' (gemini_cli) fails, secondary 'gpt' (openai_compatible) succeeds.
        m_run.return_value = mock.MagicMock(returncode=2, stdout="", stderr="gemini error")
        m_post.return_value = _resp({"choices": [{"message": {"content": "gpt response"}}]})
        
        # We need both providers to be enabled and resolve.
        # Ensure 'gem' is primary.
        with mock.patch("aether.ai_client.primary", return_value="gem"), \
             mock.patch("aether.ai_client.enabled_providers", return_value=["gem", "gpt"]), \
             self._providers({"gem": _GEMINI, "gpt": _GPT}):
            out = ai_client.evaluate("SYS", "USR")
        
        self.assertEqual(out, "gpt response")
        m_run.assert_called_once()
        m_post.assert_called_once()

    @mock.patch.object(ai_client, "_resolve_key", return_value="KEY")
    @mock.patch("aether.ai_client.requests.post")
    @mock.patch("aether.ai_client.subprocess.run")
    def test_chat_fallback_on_primary_failure(self, m_run, m_post, _key):
        # Primary provider 'gem' (gemini_cli) fails, secondary 'gpt' (openai_compatible) succeeds.
        m_run.return_value = mock.MagicMock(returncode=2, stdout="", stderr="gemini error")
        m_post.return_value = _resp({"choices": [{"message": {"content": "gpt chat response"}}]})
        
        history = [{"role": "user", "content": "hello"}]
        with mock.patch("aether.ai_client.primary", return_value="gem"), \
             mock.patch("aether.ai_client.enabled_providers", return_value=["gem", "gpt"]), \
             self._providers({"gem": _GEMINI, "gpt": _GPT}):
            out = ai_client.chat(history, system="SYS")
            
        self.assertEqual(out, "gpt chat response")
        m_run.assert_called_once()
        m_post.assert_called_once()


class TestAIClientParsers(unittest.TestCase):

    def test_parse_openai_rejects_non_dict_payload(self):
        with self.assertRaises(ValueError):
            ai_client._parse_openai_response(_resp(["not", "a", "dict"]))

    def test_parse_openai_rejects_empty_choices(self):
        with self.assertRaises(ValueError):
            ai_client._parse_openai_response(_resp({"choices": []}))

    def test_parse_anthropic_joins_text_blocks(self):
        out = ai_client._parse_anthropic_response(
            _resp({"content": [{"type": "text", "text": "foo"},
                               {"type": "text", "text": "bar"}]}))
        self.assertEqual(out, "foobar")


if __name__ == "__main__":
    unittest.main()
