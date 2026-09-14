# End-of-turn response verifier (Claude + Gemini)

One set of checks, two runtimes. The logic lives once in
[`scripts/hooks/response_verifier_core.py`](../../scripts/hooks/response_verifier_core.py);
each runtime has a thin adapter that maps the core's decision to its own hook contract.

| Runtime | Hook event | Adapter | "block" means |
|---|---|---|---|
| Claude Code | `Stop` | [`.claude/hooks/verify_stop.py`](verify_stop.py) | re-prompt with feedback (a nudge) |
| Gemini CLI | `AfterAgent` | [`.gemini/hooks/response_verifier.py`](../../.gemini/hooks/response_verifier.py) | **retry the whole turn** |

Because `deny`/`block` differs, the adapters differ on purpose:

- **Claude Stop** injects the two-layer verification **REMINDER** once per turn
  (gated on `stop_hook_active`), *plus* a concrete violation reason when one is
  detectable. A once-per-turn nudge is safe.
- **Gemini AfterAgent** denies **only** on a concrete content violation (elided code
  or an unsubstantiated systems-health claim). It never denies just to nudge —
  `deny` retries the turn, so nudging would loop.

Both **fail open**: any error allows the turn to end.

## What it checks

1. **Lazy / elided code** — "… rest of code", "implementation goes here", etc.,
   detected **only inside fenced code blocks** so prose that *quotes* a marker (a code
   review, this README) does not trip it. (Bare `TODO:` is intentionally not flagged.)
2. **Unsubstantiated health claims** — "all systems nominal", "E\*TRADE is online", …
   flagged only when **no diagnostic tool ran this turn** (evidence read from the
   transcript's current turn, not from a date string).

## Wiring

### Gemini
Already wired in [`.gemini/settings.json`](../../.gemini/settings.json). The
`"tools": { "enableHooks": true }` switch is required — without it the hook is dormant.

### Claude Code
This script is **not** auto-activated just by living in the repo. Add a `Stop` hook to
**one** settings file:

Project-scoped (`.claude/settings.json`, applies in this repo):
```json
{
  "hooks": {
    "Stop": [
      { "hooks": [ { "type": "command",
                     "command": "python \"$CLAUDE_PROJECT_DIR/.claude/hooks/verify_stop.py\"" } ] }
    ]
  }
}
```

> **Migration note:** if you already run a global standalone stop gate at
> `~/.claude/hooks/verify_stop.py`, wiring this one *too* fires the reminder twice per
> turn. Either point your global config at this file, or drop the global copy — so the
> reminder text has a single home (this core).

## Known limitation
Gemini CLI issue google-gemini/gemini-cli#15712: `AfterAgent` may not fire on a
text-only final response (no tool calls) — exactly the plain-prose "all nominal" case
the claim-check targets. Treat the Gemini side as advisory until that is fixed upstream.
