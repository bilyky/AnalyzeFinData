"""
Tests for sell_eval.py — the advisory exit-evaluation layer.
ai_client.evaluate is mocked; no real AI calls.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import sell_eval


_CTX = {"symbol": "RPD", "action": "SELL", "reason": "momentum decay",
        "cost": 7.05, "price": 11.47, "pnl_pct": 62.7, "s10": -4.2, "l60": -1.6,
        "stop": 7.0, "sma50": 10.0, "pgr": "Bu", "patterns": "MACD+"}


class TestBuildPrompt(unittest.TestCase):
    def test_includes_key_fields(self):
        p = sell_eval.build_user_prompt(_CTX)
        for token in ["RPD", "engine_action: SELL", "pnl_pct: 62.7", "combined: -5.8",
                      "sma50: 10.00", "pgr: Bu"]:
            self.assertIn(token, p)


class TestParse(unittest.TestCase):
    def test_clean_json(self):
        out = sell_eval._parse('{"verdict": "FLAG-FOR-REVIEW", "note": "winner"}')
        self.assertEqual(out, {"verdict": "FLAG-FOR-REVIEW", "note": "winner"})

    def test_markdown_fenced_json(self):
        out = sell_eval._parse('```json\n{"verdict":"AGREE","note":"ok"}\n```')
        self.assertEqual(out["verdict"], "AGREE")

    def test_freetext_fallback_picks_verdict(self):
        out = sell_eval._parse("I would FLAG-FOR-REVIEW this one, it's a winner.")
        self.assertEqual(out["verdict"], "FLAG-FOR-REVIEW")

    def test_invalid_verdict_rejected(self):
        self.assertEqual(sell_eval._parse('{"verdict":"MAYBE","note":"x"}'), {})

    def test_empty_text(self):
        self.assertEqual(sell_eval._parse(""), {})


class TestEvaluateExit(unittest.TestCase):
    def test_returns_verdict_with_provider(self):
        with mock.patch("sell_eval.ai_client.primary", return_value="github_gpt"), \
             mock.patch("sell_eval.ai_client.evaluate",
                        return_value='{"verdict":"FLAG-FOR-REVIEW","note":"selling a +63% winner"}'):
            out = sell_eval.evaluate_exit(_CTX)
        self.assertEqual(out["verdict"], "FLAG-FOR-REVIEW")
        self.assertEqual(out["provider"], "github_gpt")
        self.assertIn("winner", out["note"])

    def test_no_provider_returns_empty(self):
        with mock.patch("sell_eval.ai_client.primary", return_value=None):
            self.assertEqual(sell_eval.evaluate_exit(_CTX), {})

    def test_ai_returns_blank_yields_empty(self):
        with mock.patch("sell_eval.ai_client.primary", return_value="github_gpt"), \
             mock.patch("sell_eval.ai_client.evaluate", return_value=""):
            self.assertEqual(sell_eval.evaluate_exit(_CTX), {})

    def test_explicit_provider_passed_through(self):
        with mock.patch("sell_eval.ai_client.evaluate",
                        return_value='{"verdict":"AGREE","note":"ok"}') as ev:
            out = sell_eval.evaluate_exit(_CTX, provider="claude")
        self.assertEqual(out["provider"], "claude")
        self.assertEqual(ev.call_args.kwargs["provider"], "claude")


if __name__ == "__main__":
    unittest.main()
