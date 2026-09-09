"""Tests for the generic response verifier core shared by the Claude Stop hook and
the Gemini AfterAgent hook (``scripts/hooks/response_verifier_core.py``).

These lock the exact defects the original branch shipped, as red-green cases:
  * the date-substring EVIDENCE gate (bypass + 2026-09-01 time-bomb) -> now a real
    transcript tool-call signal;
  * the ``TODO:`` placeholder FALSE-POSITIVE on ordinary review prose -> now code-fence
    scoped and TODO dropped;
so a regression to either behaviour fails here.

The core genuinely reads the transcript from disk, so the evidence/last-message tests
write REAL temp transcript files (isolating the filesystem path, not mocking the unit
under test) and assert the decision the core returns.
"""
import importlib.util
import os
import shutil
import tempfile
import unittest

_CORE_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "scripts", "hooks",
                 "response_verifier_core.py")
)
_spec = importlib.util.spec_from_file_location("response_verifier_core", _CORE_PATH)
core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(core)


class _TranscriptMixin(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix="rvtest_")

    def tearDown(self):
        shutil.rmtree(self._dir, ignore_errors=True)

    def _write(self, name, lines):
        path = os.path.join(self._dir, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
        return path


class TestFindLazyCode(unittest.TestCase):
    def test_elision_inside_fence_is_flagged(self):
        text = "Here is the patch:\n```python\ndef f():\n    # ... rest of code\n```"
        self.assertTrue(core.find_lazy_code(text))

    def test_marker_in_prose_only_is_ignored(self):
        # The branch's version denied this (a review quoting a placeholder). It must NOT.
        text = "The diff leaves a `... rest of code` stub in prose but no code fence here."
        self.assertEqual(core.find_lazy_code(text), [])

    def test_todo_is_not_a_placeholder(self):
        # Red-green vs the branch: '...leftover TODO: refactor later.' was denied.
        text = "Reviewing: there is a leftover TODO: refactor later in their code."
        self.assertEqual(core.find_lazy_code(text), [])

    def test_todo_in_fence_still_not_flagged(self):
        text = "```python\nx = 1  # TODO: refactor\n```"
        self.assertEqual(core.find_lazy_code(text), [])


class TestEvidenceGate(_TranscriptMixin):
    def test_bypass_fixed_claim_without_tool_call_denies(self):
        # Branch bug: mentioning the date bypassed the gate. Now: no tool call this
        # turn -> unsubstantiated, regardless of any date in the text.
        tpath = self._write("t.jsonl", [
            '{"role":"user","content":"status?"}',
            '{"role":"assistant","content":"working"}',
        ])
        payload = {
            "prompt_response": "All systems nominal as of 2026-08-29 (no check run).",
            "transcript_path": tpath,
        }
        out = core.decide_gemini(payload)
        self.assertEqual(out["decision"], "deny")
        self.assertIn("unsubstantiated", out["systemMessage"])

    def test_timebomb_fixed_claim_with_tool_call_allows(self):
        # Branch bug: a real check dated 2026-09-01 was DENIED (literal '2026-08' gone).
        # Now: a tool call this turn substantiates the claim regardless of date.
        tpath = self._write("t.jsonl", [
            '{"role":"user","content":"status?"}',
            '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"ping"}]}}',
            '{"role":"assistant","content":"checked"}',
        ])
        payload = {
            "prompt_response": "All systems nominal (verified via ping on 2026-09-01).",
            "transcript_path": tpath,
        }
        self.assertEqual(core.decide_gemini(payload)["decision"], "allow")

    def test_no_transcript_fails_open(self):
        # No evidence signal available -> do not deny (honest fail-open).
        payload = {"prompt_response": "all systems nominal", "transcript_path": None}
        self.assertEqual(core.decide_gemini(payload)["decision"], "allow")

    def test_ran_diagnostic_tri_state(self):
        with_tool = self._write("a.jsonl", [
            '{"role":"user","content":"x"}',
            '{"role":"assistant","content":"[tool_use ping]"}',
        ])
        no_tool = self._write("b.jsonl", [
            '{"role":"user","content":"x"}',
            '{"role":"assistant","content":"plain"}',
        ])
        self.assertIs(core.ran_diagnostic(with_tool), True)
        self.assertIs(core.ran_diagnostic(no_tool), False)
        self.assertIsNone(core.ran_diagnostic(None))
        self.assertIsNone(core.ran_diagnostic(os.path.join(self._dir, "missing.jsonl")))


class TestDecideGemini(unittest.TestCase):
    def test_lazy_code_denies_regardless_of_evidence(self):
        payload = {"prompt_response": "```py\n# ... rest of code\n```"}
        out = core.decide_gemini(payload)
        self.assertEqual(out["decision"], "deny")
        self.assertIn("lazy-code", out["systemMessage"])

    def test_clean_response_allows(self):
        self.assertEqual(
            core.decide_gemini({"prompt_response": "Done. Ran tests: 573 OK."})["decision"],
            "allow",
        )

    def test_non_dict_payload_allows(self):
        self.assertEqual(core.decide_gemini("nope")["decision"], "allow")

    def test_no_decorative_emoji_in_output(self):
        # Branch shipped a 🚨 in systemMessage (cp1252 crash risk on Windows console).
        out = core.decide_gemini({"prompt_response": "```py\n# ... rest of code\n```"})
        blob = (out.get("reason", "") + out.get("systemMessage", ""))
        self.assertTrue(blob.isascii(), "verifier output must stay ASCII")


class TestDecideClaudeStop(_TranscriptMixin):
    def test_reminder_once_then_allow(self):
        # First stop (no loop flag) -> block with REMINDER; second (flag set) -> allow.
        first = core.decide_claude_stop({"stop_hook_active": False})
        self.assertEqual(first["decision"], "block")
        self.assertIn("STOP-GATE", first["reason"])
        self.assertIsNone(core.decide_claude_stop({"stop_hook_active": True}))

    def test_violation_prepended_to_reminder(self):
        # A lazy-code final assistant message is surfaced AND the reminder still fires.
        tpath = self._write("t.jsonl", [
            '{"role":"user","content":"patch it"}',
            '{"type":"assistant","message":{"content":[{"type":"text",'
            '"text":"```py\\n# ... rest of code\\n```"}]}}',
        ])
        out = core.decide_claude_stop({"transcript_path": tpath})
        self.assertEqual(out["decision"], "block")
        self.assertIn("elided", out["reason"].lower())   # the violation reason...
        self.assertIn("STOP-GATE", out["reason"])         # ...prepended to the reminder

    def test_non_dict_allows(self):
        self.assertIsNone(core.decide_claude_stop("nope"))


class TestLastAssistantText(_TranscriptMixin):
    def test_extracts_content_list_text(self):
        tpath = self._write("t.jsonl", [
            '{"role":"user","content":"hi"}',
            '{"type":"assistant","message":{"content":[{"type":"text","text":"hello world"}]}}',
        ])
        self.assertEqual(core.last_assistant_text(tpath), "hello world")

    def test_missing_file_returns_empty(self):
        self.assertEqual(
            core.last_assistant_text(os.path.join(self._dir, "nope.jsonl")), ""
        )


if __name__ == "__main__":
    unittest.main()
