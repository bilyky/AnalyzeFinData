#!/usr/bin/env python
"""Generic, runtime-agnostic end-of-turn response verifier.

Single source of truth shared by two thin adapters:
  * ``.claude/hooks/verify_stop.py``        -> Claude Code **Stop** hook
  * ``.gemini/hooks/response_verifier.py``  -> Gemini CLI **AfterAgent** hook

Both runtimes want the same guarantee: never let a turn end on an unverified
systems-health claim, or on elided / lazy ("... rest of code") stub code. The two
grew up separately (a reminder-injector on Claude, a content-scanner on Gemini);
this core unifies the *detection* so there is one place to fix and test, while each
adapter keeps its own runtime semantics.

Why the split of concerns matters — "block" means different things per runtime:

  * Claude **Stop**: block == re-prompt the agent with feedback (a nudge). Injecting
    a once-per-turn verification reminder is cheap and safe, so the Claude adapter
    emits it whenever no harder violation fired.
  * Gemini **AfterAgent**: deny == **RETRY the whole turn**. Denying merely to inject
    a reminder would loop the turn, so the Gemini adapter denies ONLY on a concrete
    content violation, never just to nudge.

The core therefore exposes pure, schema-free primitives...
  * ``scan(response_text, transcript_path) -> Violation | None``  (content checks)
  * ``REMINDER``                                                  (two-layer nudge)
  * ``ran_diagnostic(transcript_path) -> bool | None``           (evidence signal)
  * ``last_assistant_text(transcript_path) -> str``              (for runtimes whose
        hook payload does not carry the response text, e.g. Claude Stop)

...plus two thin per-runtime deciders (``decide_gemini`` / ``decide_claude_stop``)
that map those primitives to each runtime's JSON contract. The deciders are pure
(dict in -> dict/None out) so they are unit-testable without stdin/stdout.

Everything FAILS OPEN: any error anywhere allows the turn to end. A governance hook
must never trap the agent in a non-stopping state.
"""
from __future__ import annotations

import json
import re
from collections import namedtuple

Violation = namedtuple("Violation", ["kind", "reason"])

# The two-layer end-of-turn reminder. Condensed from the original verbose Claude Stop
# gate (which lived only in ~/.claude/hooks/verify_stop.py) into a terse single-line
# nudge, per the "reduce chatting" directive: same two-layer principle (verify atoms
# AND the story around them; say "I don't know" rather than invent), minus the long
# framing and the mandated "reply exactly ALL VERIFIED" ritual that added per-turn noise.
REMINDER = (
    "STOP-GATE: before finishing, verify every claim with a tool call THIS turn - "
    "both the discrete facts (PR/commit/test state, command output, any "
    "'done/passed/fixed') and the causal/temporal story around them ('X caused Y', "
    "'took a week', 'always/never'). Can't verify it? Say 'I don't know' - don't "
    "invent. Fix any unverified claim now, then stop."
)

# Unambiguous "I elided real code" / "fill this in later" markers. Matched
# case-INSENSITIVELY and ONLY inside fenced code blocks, so prose that *quotes* a
# marker (a code review discussing a TODO, this very docstring) never trips it.
# NOTE: bare ``TODO:`` is deliberately NOT here — a review/answer legitimately quotes
# it constantly; the branch's version false-positived on ordinary review text.
ELISION_MARKERS = (
    "... rest of code",
    "...rest of code",
    "rest of code unchanged",
    "rest of the code unchanged",
    "... existing code",
    "...existing code",
    "existing code unchanged",
    "// ... unchanged",
    "# ... unchanged",
    "implementation goes here",
    "your code here",
    "rest of implementation",
    "code omitted for brevity",
    "code omitted",
)

# Systems-health assertions that require live evidence. Each is only a violation when
# NO diagnostic tool ran this turn (see ``ran_diagnostic``) — evidenced claims pass.
HEALTH_CLAIMS = (
    "all systems nominal",
    "all systems operational",
    "all systems go",
    "e*trade is online",
    "etrade is online",
    "everything is green",
    "everything's green",
)

_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)

# Generic tool-call fingerprints across runtimes' transcript formats (lowercased).
_TOOL_MARKERS = (
    "tool_use", "toolrequest", "functioncall", "function_call",
    "tool_call", '"role":"tool"', '"role": "tool"', '"type":"tool"',
)
# Fingerprints that a transcript entry is a *user* turn (to bound "this turn").
_USER_MARKERS = (
    '"role":"user"', '"role": "user"', '"type":"user"', '"type": "user"',
)


def _fenced_code(text: str) -> str:
    """Concatenate all fenced code-block bodies in ``text`` (lowercased)."""
    return "\n".join(_FENCE_RE.findall(text or "")).lower()


def find_lazy_code(text: str) -> list:
    """Elision markers present INSIDE fenced code blocks. Prose is ignored, so a
    review that quotes a marker does not trip. Returns the list of markers found."""
    fenced = _fenced_code(text)
    return [m for m in ELISION_MARKERS if m in fenced]


def ran_diagnostic(transcript_path):
    """Best-effort: did the agent invoke a tool (any diagnostic) in the CURRENT turn?

    Reads the transcript, bounds to everything after the last user-turn line, and
    looks for a tool-call fingerprint. Returns:
        True  -> a tool call is present this turn  (a health claim is substantiated)
        False -> no tool call this turn            (a bare health claim is not)
        None  -> transcript missing/unreadable     (caller FAILS OPEN)

    This replaces the branch's ``"2026-08" in response_text`` proxy, which both let
    any response that merely mentioned the year-month bypass the gate AND hard-broke
    on 2026-09-01. A tool-call signal is real evidence and carries no calendar bomb;
    when the format is unrecognized it declines to judge rather than guess wrong.
    """
    if not transcript_path:
        return None
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except Exception:
        return None
    if not lines:
        return None
    start = 0
    for i in range(len(lines) - 1, -1, -1):
        low = lines[i].lower()
        if any(m in low for m in _USER_MARKERS):
            start = i
            break
    turn = "\n".join(lines[start:]).lower()
    return any(m in turn for m in _TOOL_MARKERS)


def scan(response_text, transcript_path=None):
    """Pure content check. Returns a ``Violation`` or ``None``. Never raises.

    Order: lazy code first (unconditional), then health claim (evidence-gated).
    """
    try:
        text = response_text or ""
        lazy = find_lazy_code(text)
        if lazy:
            found = ", ".join(sorted(set(lazy)))
            return Violation(
                "lazy-code",
                "Response contains elided/placeholder code inside a code block "
                f"({found}). Emit the COMPLETE code (no 'rest of code' stubs), "
                "then finish.",
            )
        low = text.lower()
        claimed = [c for c in HEALTH_CLAIMS if c in low]
        if claimed and ran_diagnostic(transcript_path) is False:
            found = ", ".join(sorted(set(claimed)))
            return Violation(
                "unsubstantiated-claim",
                "Response asserts a systems-health claim "
                f"({found}) but no diagnostic tool was invoked this turn to back it. "
                "Run the check and cite its output this turn, or drop the claim.",
            )
        return None
    except Exception:
        return None


def _extract_text(obj: dict) -> str:
    """Pull the text out of one assistant transcript entry (tolerant of shapes)."""
    msg = obj.get("message", obj)
    if isinstance(msg, str):
        return msg
    if isinstance(msg, dict):
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(item.get("text", ""))
                elif isinstance(item, str):
                    parts.append(item)
            return "\n".join(parts)
    return ""


def last_assistant_text(transcript_path) -> str:
    """Best-effort extraction of the final assistant message text from a JSONL
    transcript (Claude Code format). Returns '' on any problem — so a runtime whose
    payload lacks the response text still gets the reminder (just no content check).
    Never raises."""
    if not transcript_path:
        return ""
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except Exception:
        return ""
    for raw in reversed(lines):
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("type") == "assistant" or obj.get("role") == "assistant":
            return _extract_text(obj)
    return ""


# ---------------------------------------------------------------------------
# Per-runtime deciders (pure: payload dict -> output dict / None). Adapters below
# only handle stdin/stdout; all policy lives here so it is testable.
# ---------------------------------------------------------------------------

def decide_gemini(payload) -> dict:
    """Gemini **AfterAgent**: deny ONLY on a concrete content violation.

    (deny == retry the turn, so we must not deny just to nudge — that would loop.)
    """
    if not isinstance(payload, dict):
        return {"decision": "allow"}
    v = scan(payload.get("prompt_response") or "", payload.get("transcript_path"))
    if v is None:
        return {"decision": "allow"}
    return {
        "decision": "deny",
        "reason": v.reason,
        "systemMessage": f"Response blocked by response verifier ({v.kind}).",
    }


def decide_claude_stop(payload):
    """Claude **Stop**: block on a violation OR (once per turn) to inject the reminder.

    Returns the hook-output dict, or ``None`` to allow the stop (print nothing).
    Strict superset of the original stop gate: still the once-per-turn REMINDER,
    now with a concrete violation reason prepended when one is detectable.
    """
    if not isinstance(payload, dict):
        return None
    if payload.get("stop_hook_active"):
        return None  # already fired once this turn -> allow stop (no loop)
    text = last_assistant_text(payload.get("transcript_path"))
    v = scan(text, payload.get("transcript_path"))
    if v is not None:
        return {"decision": "block", "reason": v.reason + "\n\n" + REMINDER}
    return {"decision": "block", "reason": REMINDER}
