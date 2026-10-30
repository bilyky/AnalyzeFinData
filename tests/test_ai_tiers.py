"""
Tiered, provider-agnostic routing in ai_client.

PROD 2026-09-24..10-05: every advisory AI call failed for 12 days (the primary's host was
misspelled and the only fallback was a retired provider) and nothing showed it, because
ai_client logged failures but never successes. These tests pin the contract that replaces
the single `primary` chain:

  - a call names a tier ("cheap" / "strong"), not a provider; the chain is that tier's
    providers, then the "strong" tier's (escalation), then primary, then every other
    enabled provider — deduped, unusable ones skipped;
  - no tier keeps the old order (primary, then the other enabled providers);
  - an explicit provider= still means exactly that provider;
  - an empty answer is a failure, so the next provider gets the call;
  - every success is logged with tier, provider, model and timing;
  - each provider must name its model (no hidden default model);
  - the anthropic transport merges extra_body like openai_compatible does;
  - AETHER_AI_CAPTURE=<file> appends the prompt to a JSONL file (off by default).
All network is mocked.
"""
import ast
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import aether.ai_client as ai_client
import extract_email_intel as ex
from aether import stock_compare


def _p(**kw):
    base = {"type": "openai_compatible", "enabled": True, "endpoint": "https://example/v1/chat/completions",
            "model": "m-x", "api_key_source": "env:FAKE"}
    base.update(kw)
    return base


def _resp(content):
    r = mock.MagicMock()
    r.json.return_value = {"choices": [{"message": {"content": content}}]}
    r.raise_for_status.return_value = None
    return r


class _Cfg:
    """Stand-in for CFG's ai fields."""
    def __init__(self, providers, primary="", tiers=None):
        self.ai_providers = providers
        self.ai_primary = primary
        self.ai_tiers = tiers or {}


def _with_cfg(cfg):
    return mock.patch.object(ai_client, "CFG", cfg)


class TestTierChain(unittest.TestCase):

    def setUp(self):
        self.providers = {n: _p(model=f"model-{n}") for n in ("a", "b", "c", "d")}

    def test_tier_then_strong_then_primary_then_rest(self):
        cfg = _Cfg(self.providers, primary="a", tiers={"cheap": ["b"], "strong": ["c"]})
        with _with_cfg(cfg), mock.patch.object(ai_client, "_resolve_key", return_value="K"):
            self.assertEqual(ai_client.provider_chain(tier="cheap"), ["b", "c", "a", "d"])
            self.assertEqual(ai_client.provider_chain(tier="strong"), ["c", "a", "b", "d"])

    def test_no_tier_keeps_primary_then_enabled_order(self):
        cfg = _Cfg(self.providers, primary="c", tiers={"cheap": ["b"]})
        with _with_cfg(cfg), mock.patch.object(ai_client, "_resolve_key", return_value="K"):
            self.assertEqual(ai_client.provider_chain(), ["c", "a", "b", "d"])

    def test_unknown_or_disabled_tier_members_are_skipped(self):
        providers = dict(self.providers, b=_p(enabled=False))
        cfg = _Cfg(providers, primary="a", tiers={"cheap": ["ghost", "b", "d"]})
        with _with_cfg(cfg), mock.patch.object(ai_client, "_resolve_key", return_value="K"):
            self.assertEqual(ai_client.provider_chain(tier="cheap"), ["d", "a", "c"])

    def test_unknown_tier_name_falls_back_to_default_order(self):
        cfg = _Cfg(self.providers, primary="b", tiers={"cheap": ["c"]})
        with _with_cfg(cfg), mock.patch.object(ai_client, "_resolve_key", return_value="K"):
            self.assertEqual(ai_client.provider_chain(tier="nonesuch"), ["b", "a", "c", "d"])

    def test_note_keys_and_non_lists_are_not_tiers(self):
        cfg = _Cfg(self.providers, primary="a",
                   tiers={"_note": "free text", "_draft": ["a"], "cheap": ["b"], "broken": "c"})
        with _with_cfg(cfg):
            self.assertEqual(ai_client.configured_tiers(), {"cheap": ["b"]})

    def test_explicit_provider_is_the_whole_chain(self):
        cfg = _Cfg(self.providers, primary="a", tiers={"cheap": ["b"]})
        with _with_cfg(cfg), mock.patch.object(ai_client, "_resolve_key", return_value="K"):
            self.assertEqual(ai_client.provider_chain(provider="d", tier="cheap"), ["d"])


class TestRouting(unittest.TestCase):

    def setUp(self):
        self.cfg = _Cfg({"a": _p(model="model-a"), "b": _p(model="model-b")},
                        primary="a", tiers={"cheap": ["b", "a"]})

    @mock.patch.object(ai_client, "_resolve_key", return_value="K")
    @mock.patch("aether.ai_client.requests.post")
    def test_cheap_tier_call_goes_to_the_tier_provider(self, m_post, _key):
        m_post.return_value = _resp("from b")
        with _with_cfg(self.cfg):
            text, name = ai_client.evaluate_with_provider("S", "U", tier="cheap")
        self.assertEqual((text, name), ("from b", "b"))
        self.assertEqual(m_post.call_args.kwargs["json"]["model"], "model-b")

    @mock.patch.object(ai_client, "_resolve_key", return_value="K")
    @mock.patch("aether.ai_client.requests.post")
    def test_empty_answer_falls_through_to_the_next_provider(self, m_post, _key):
        m_post.side_effect = [_resp("   "), _resp("real answer")]
        with _with_cfg(self.cfg):
            text, name = ai_client.evaluate_with_provider("S", "U", tier="cheap")
        self.assertEqual((text, name), ("real answer", "a"))
        self.assertEqual(m_post.call_count, 2)

    @mock.patch.object(ai_client, "_resolve_key", return_value="K")
    @mock.patch("aether.ai_client.requests.post")
    def test_every_provider_empty_raises(self, m_post, _key):
        m_post.side_effect = [_resp(""), _resp("")]
        with _with_cfg(self.cfg), self.assertRaises(ValueError):
            ai_client.evaluate("S", "U", tier="cheap")

    @mock.patch.object(ai_client, "_resolve_key", return_value="K")
    @mock.patch("aether.ai_client.requests.post")
    def test_chat_with_provider_reports_who_answered(self, m_post, _key):
        m_post.side_effect = [RuntimeError("down"), _resp("hi")]
        with _with_cfg(self.cfg):
            text, name = ai_client.chat_with_provider([{"role": "user", "content": "x"}],
                                                      system="S", tier="cheap")
        self.assertEqual((text, name), ("hi", "a"))

    def test_no_usable_provider_raises(self):
        with _with_cfg(_Cfg({})), self.assertRaises(RuntimeError):
            ai_client.evaluate("S", "U", tier="cheap")


class TestSuccessLogging(unittest.TestCase):

    @mock.patch.object(ai_client, "_resolve_key", return_value="K")
    @mock.patch("aether.ai_client.requests.post")
    def test_success_is_logged_with_tier_provider_and_model(self, m_post, _key):
        m_post.return_value = _resp("ok answer")
        cfg = _Cfg({"b": _p(model="model-b")}, tiers={"cheap": ["b"]})
        logger = mock.MagicMock()
        with _with_cfg(cfg), mock.patch.object(ai_client, "_get_logger", return_value=logger):
            ai_client.evaluate("SYS", "USER", tier="cheap")
        infos = [c for c in logger.info.call_args_list if "AI call ok" in str(c.args[0])]
        self.assertEqual(len(infos), 1, logger.info.call_args_list)
        extra = infos[0].kwargs["extra"]
        self.assertEqual(extra["ai_tier"], "cheap")
        self.assertEqual(extra["ai_provider"], "b")
        self.assertEqual(extra["ai_model"], "model-b")
        self.assertEqual(extra["in_chars"], len("SYS") + len("USER"))
        self.assertEqual(extra["out_chars"], len("ok answer"))
        self.assertIn("ms", extra)


class TestProviderContract(unittest.TestCase):

    def test_transport_registry_covers_the_documented_types(self):
        self.assertEqual(set(ai_client.TRANSPORTS), {"openai_compatible", "anthropic", "gemini_cli"})

    @mock.patch.object(ai_client, "_resolve_key", return_value="K")
    @mock.patch("aether.ai_client.requests.post")
    def test_provider_without_model_is_rejected_before_any_request(self, m_post, _key):
        cfg = _Cfg({"claude": {"type": "anthropic", "enabled": True, "api_key_source": "env:FAKE"}})
        with _with_cfg(cfg), self.assertRaises(RuntimeError) as cm:
            ai_client.evaluate("S", "U", provider="claude")
        self.assertIn("model", str(cm.exception))
        m_post.assert_not_called()

    @mock.patch.object(ai_client, "_resolve_key", return_value="K")
    @mock.patch("aether.ai_client.requests.post")
    def test_anthropic_merges_extra_body_but_core_fields_win(self, m_post, _key):
        r = mock.MagicMock()
        r.json.return_value = {"content": [{"type": "text", "text": "done"}]}
        r.raise_for_status.return_value = None
        m_post.return_value = r
        cfg = _Cfg({"claude": {"type": "anthropic", "enabled": True, "api_key_source": "env:FAKE",
                               "model": "claude-x",
                               "extra_body": {"output_config": {"effort": "low"},
                                              "model": "evil", "max_tokens": 9999}}})
        with _with_cfg(cfg):
            ai_client.evaluate("S", "U", provider="claude", max_tokens=80)
        body = m_post.call_args.kwargs["json"]
        self.assertEqual(body["output_config"], {"effort": "low"})
        self.assertEqual(body["model"], "claude-x")
        self.assertEqual(body["max_tokens"], 80)


class TestPromptCapture(unittest.TestCase):

    @mock.patch.object(ai_client, "_resolve_key", return_value="K")
    @mock.patch("aether.ai_client.requests.post")
    def test_capture_appends_jsonl_only_when_env_is_set(self, m_post, _key):
        m_post.return_value = _resp("ok")
        cfg = _Cfg({"b": _p()}, tiers={"cheap": ["b"]})
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "cap.jsonl")
            with _with_cfg(cfg), mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("AETHER_AI_CAPTURE", None)
                ai_client.evaluate("S", "U", tier="cheap", max_tokens=42)
                self.assertFalse(os.path.exists(path))
                os.environ["AETHER_AI_CAPTURE"] = path
                ai_client.evaluate("S", "U", tier="cheap", max_tokens=42)
            with open(path, encoding="utf-8") as f:
                rows = [json.loads(x) for x in f]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["system"], "S")
        self.assertEqual(rows[0]["messages"], [{"role": "user", "content": "U"}])
        self.assertEqual(rows[0]["max_tokens"], 42)
        self.assertEqual(rows[0]["tier"], "cheap")


_REPO = os.path.join(os.path.dirname(__file__), "..")
_CALL_NAMES = {"evaluate", "evaluate_with_provider", "chat", "chat_with_provider"}


def _ai_calls_without_tier():
    """Every ai_client.<call>(...) in production code that does not pass tier=."""
    missing = []
    for root, dirs, files in os.walk(_REPO):
        dirs[:] = [d for d in dirs if not d.startswith((".", "_wt_", "venv", "tests", "__pycache__"))]
        for fn in files:
            if not fn.endswith(".py") or fn == "ai_client.py":
                continue
            path = os.path.join(root, fn)
            try:
                with open(path, encoding="utf-8") as f:
                    tree = ast.parse(f.read())
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in _CALL_NAMES
                        and isinstance(node.func.value, ast.Name) and node.func.value.id == "ai_client"
                        and not any(k.arg == "tier" for k in node.keywords)):
                    missing.append(f"{os.path.relpath(path, _REPO)}:{node.lineno}")
    return missing


class TestCallSitesNameATier(unittest.TestCase):
    """Call sites ask for a tier, never a provider, so swapping providers is a config edit."""

    def test_every_production_ai_call_passes_a_tier(self):
        self.assertEqual(_ai_calls_without_tier(), [])


class TestCallersUseTheFallbackChain(unittest.TestCase):
    """stock_compare and extract_email_intel used to pin provider=primary(), which skipped
    every fallback; they now route by tier and report the provider that answered."""

    def test_stock_compare_routes_by_strong_tier_without_pinning_a_provider(self):
        with mock.patch.object(stock_compare.ai_client, "primary", return_value="fake"), \
             mock.patch.object(stock_compare.Path, "read_text", return_value="RUBRIC"), \
             mock.patch.object(stock_compare, "render_for_summary", return_value="BLOCK"), \
             mock.patch.object(stock_compare.ai_client, "evaluate_with_provider",
                               return_value=("RANKED", "backup")) as m:
            summary, reason = stock_compare.summarize_comparison({"symbols": [], "rows": []})
        self.assertEqual((summary, reason), ("RANKED", None))
        self.assertIsNone(m.call_args.kwargs.get("provider"))
        self.assertEqual(m.call_args.kwargs.get("tier"), "strong")

    def test_extract_labels_the_provider_that_answered(self):
        intel = json.dumps({"confidence": "HIGH", "pitch_ratio": 0.1, "dated_catalysts": [],
                            "supply_chain_facts": [], "tickers_mentioned": []})
        with mock.patch.object(ex.ai_client, "primary", return_value="gpt"), \
             mock.patch.object(ex.ai_client, "evaluate_with_provider",
                               return_value=(intel, "backup")) as m, \
             mock.patch("data_api.read_research", return_value={"rows": []}):
            result = ex.extract("Subj", "Body", verify=False)
        self.assertEqual(result["_provider"], "backup")
        self.assertIsNone(m.call_args.kwargs.get("provider"))
        self.assertEqual(m.call_args.kwargs.get("tier"), "cheap")


if __name__ == "__main__":
    unittest.main()
