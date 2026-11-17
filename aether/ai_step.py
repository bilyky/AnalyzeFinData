"""One AI step on AETHER's own AI layer, for scheduled jobs that used to run through the Gemini CLI.

The Gemini CLI agent tasks failed with HTTP 403 ("prohibited by organization's policy") on PROD every
run (RD_Scientist every Saturday from at least 2026-09-19). Jobs now do their computation in Python and
call this for the analysis: it uses ``aether.ai_client`` with the providers and tiers in config.json
(``ai``). When the step can't run (no usable provider, every provider failed, an empty answer) it is
**skipped, never faked**: the caller gets ``text=None`` plus the reason, the skip is logged as a
WARNING, and by default the owner gets one email saying why.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from aether import ai_client, notify
from aether.logger import get_logger as _get_logger

_log = _get_logger("ai_step")


@dataclass
class StepResult:
    text: Optional[str]        # the answer, or None when the step was skipped
    provider: Optional[str]    # the provider that answered
    skipped: Optional[str]     # why the step was skipped, or None


def run(name: str, system: str, user: str, *, tier: str = "strong", max_tokens: int = 2000,
        temperature: float = 0.2, notify_on_skip: bool = True) -> StepResult:
    """Run one AI step. Returns the answer, or a skip with its reason (logged; emailed unless
    ``notify_on_skip`` is False, for callers that send their own message)."""
    try:
        text, provider = ai_client.evaluate_with_provider(system, user, max_tokens=max_tokens,
                                                          temperature=temperature, tier=tier)
    except Exception as e:   # every provider failed, or none is configured for the tier
        return _skip(name, tier, f"{type(e).__name__}: {e}", notify_on_skip)
    if not (text or "").strip():
        return _skip(name, tier, f"empty answer from {provider}", notify_on_skip)
    _log.info(f"AI step '{name}': answered by {provider} ({len(text)} chars).")
    return StepResult(text.strip(), provider, None)


def _skip(name: str, tier: str, reason: str, notify_on_skip: bool) -> StepResult:
    reason = reason[:500]
    _log.warning(f"AI step '{name}' skipped (tier {tier}): {reason}")
    if notify_on_skip:
        try:
            notify.send_email(f"AETHER: AI step skipped - {name}",
                              f"The AI step '{name}' was skipped (tier '{tier}').\n\nReason: {reason}\n\n"
                              "The job's computed output is kept. Check the 'ai' providers in config.json "
                              "(keys, enabled flags, tiers) and the provider status.")
        except Exception as e:
            _log.error(f"Could not send the AI-step-skipped email: {e}")
    return StepResult(None, None, reason)
