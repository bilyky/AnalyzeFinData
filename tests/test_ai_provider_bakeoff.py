"""
scripts/diagnostics/ai_provider_bakeoff.py — runs captured real prompts through several
providers so a provider switch is chosen on answers, not on price alone.

The core (run_bakeoff / summarize) takes the call as a parameter, so these tests drive it
with a fake call: no network, no config.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "diagnostics"))
import ai_provider_bakeoff as bake


_ROWS = [
    {"tier": "cheap", "system": "S1", "messages": [{"role": "user", "content": "q1"}], "max_tokens": 50},
    {"tier": "strong", "system": "S2", "messages": [{"role": "user", "content": "q2"}], "max_tokens": 80},
]


def _fake_call(answers):
    def call(messages, system="", provider=None, max_tokens=0, temperature=0.0, tier=None):
        out = answers[provider]
        if isinstance(out, Exception):
            raise out
        return out, provider
    return call


class TestBakeoff(unittest.TestCase):

    def test_each_prompt_runs_on_each_named_provider_only(self):
        seen = []
        def call(messages, system="", provider=None, max_tokens=0, temperature=0.0, tier=None):
            seen.append((system, provider, max_tokens))
            return "ok", provider
        results = bake.run_bakeoff(_ROWS, ["a", "b"], call=call)
        self.assertEqual(len(results), 4)
        self.assertEqual(sorted(seen), sorted([("S1", "a", 50), ("S1", "b", 50),
                                               ("S2", "a", 80), ("S2", "b", 80)]))

    def test_failures_are_recorded_not_raised(self):
        results = bake.run_bakeoff(_ROWS[:1], ["good", "bad"],
                                   call=_fake_call({"good": "fine", "bad": RuntimeError("429")}))
        by = {r["provider"]: r for r in results}
        self.assertTrue(by["good"]["ok"])
        self.assertFalse(by["bad"]["ok"])
        self.assertIn("RuntimeError", by["bad"]["error"])

    def test_summary_counts_ok_and_reports_latency_per_provider(self):
        results = bake.run_bakeoff(_ROWS, ["good", "bad"],
                                   call=_fake_call({"good": "fine", "bad": RuntimeError("x")}))
        summary = {s["provider"]: s for s in bake.summarize(results)}
        self.assertEqual((summary["good"]["ok"], summary["good"]["total"]), (2, 2))
        self.assertEqual((summary["bad"]["ok"], summary["bad"]["total"]), (0, 2))
        self.assertIn("avg_ms", summary["good"])

    def test_load_prompts_reads_capture_jsonl_and_honours_limit(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "cap.jsonl")
            with open(path, "w", encoding="utf-8") as f:
                for r in _ROWS * 3:
                    f.write(json.dumps(r) + "\n")
                f.write("not json\n")
            rows = bake.load_prompts(path, limit=4)
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["system"], "S1")


if __name__ == "__main__":
    unittest.main()
