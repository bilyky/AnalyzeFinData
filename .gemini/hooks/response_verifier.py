#!/usr/bin/env python
"""Gemini CLI **AfterAgent** hook -> generic response verifier.

Thin adapter: read the AfterAgent JSON payload from stdin, delegate the decision to
the shared, runtime-agnostic core (``scripts/hooks/response_verifier_core.py``), and
print the single decision JSON to stdout. Fails OPEN on any error.

The core is shared with the Claude Code Stop hook (``.claude/hooks/verify_stop.py``)
so both runtimes enforce ONE set of checks, defined and tested in one place.

Requires ``"tools": { "enableHooks": true }`` in ``.gemini/settings.json`` to
activate. Known Gemini CLI limitation (google-gemini/gemini-cli#15712): AfterAgent
may NOT fire on a text-only final response (no tool calls) — exactly the case the
claim-check most wants to catch — so treat this as advisory, not a hard guarantee.
"""
import importlib.util
import json
import os
import sys


def _load_core():
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.normpath(
        os.path.join(here, "..", "..", "scripts", "hooks", "response_verifier_core.py")
    )
    spec = importlib.util.spec_from_file_location("response_verifier_core", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    try:
        raw = sys.stdin.read().strip()
        payload = json.loads(raw) if raw else {}
        result = _load_core().decide_gemini(payload)
    except Exception as e:  # fail open — never block the agent on a hook bug
        sys.stderr.write(f"response_verifier: {e}\n")
        result = {"decision": "allow"}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
    sys.exit(0)
