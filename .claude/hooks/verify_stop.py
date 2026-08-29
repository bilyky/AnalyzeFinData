#!/usr/bin/env python
"""Claude Code **Stop** hook -> generic response verifier.

Thin adapter over the shared, runtime-agnostic core
(``scripts/hooks/response_verifier_core.py``), so the Claude Stop gate and the Gemini
AfterAgent hook (``.gemini/hooks/response_verifier.py``) enforce ONE set of checks.

Behaviour is a strict superset of the original stand-alone stop gate: it still
injects the two-layer verification REMINDER exactly once per turn (gated on
``stop_hook_active``), and now ALSO prepends a concrete content-violation reason
(elided code / unsubstantiated health claim) when the transcript lets it detect one.

FAILS OPEN: any error allows the stop. Fires at most once per turn (no loop).

Wiring (this script is not auto-activated by being in the repo): add a Stop hook to
either project ``.claude/settings.json`` or your global ``~/.claude/settings.json``
pointing at this file — see ``.claude/hooks/README.md``. If you already run a global
standalone stop gate, replace it (or don't also wire this one) to avoid a double
reminder per turn.
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
        data = json.load(sys.stdin)
    except Exception:
        return  # unparseable stdin -> allow stop (fail-open)
    try:
        result = _load_core().decide_claude_stop(data)
    except Exception:
        return  # any error -> allow stop
    if result is not None:
        print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # never trap the agent
    sys.exit(0)
