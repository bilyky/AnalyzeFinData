"""Patch the Groq provider in Data/config.json 2026-11-04: stop the empty answers.

Groq's `openai/gpt-oss-120b` is a reasoning model. It spends max_tokens on reasoning before the
answer, and our callers ask for 5-150 tokens, so 39 of 68 PROD calls (11-02..11-04) came back
empty and fell through to Gemini (which then returned 503). This sets a token floor and a low
reasoning effort. Needs the code from PR #238 (it reads `min_max_tokens`).

Run from the repo root on PROD, after the deploy:
    venv_new\\Scripts\\python.exe docs\\prod-notes\\patch_groq_reasoning_2026_11_04.py            # dry run: prints the plan
    venv_new\\Scripts\\python.exe docs\\prod-notes\\patch_groq_reasoning_2026_11_04.py --apply    # backs up, then writes

What it changes (nothing else in the file):
- ai.providers.groq.min_max_tokens -> 1024
- ai.providers.groq.extra_body.reasoning_effort -> "low" (other extra_body keys are kept)
It never prints a key. The backup goes to Data/Backup/<timestamp>_config_groq_1104/config.json.
"""
import argparse
import datetime
import json
import os
import shutil
import sys

MIN_MAX_TOKENS = 1024
REASONING_EFFORT = "low"


def patch(cfg: dict) -> list[str]:
    """Apply the change to cfg in place and return a list of human-readable changes."""
    groq = ((cfg.get("ai") or {}).get("providers") or {}).get("groq")
    if not groq:
        raise SystemExit("ai.providers.groq is missing; nothing to patch.")
    changes = []
    if groq.get("min_max_tokens") != MIN_MAX_TOKENS:
        changes.append(f"groq.min_max_tokens: {groq.get('min_max_tokens')!r} -> {MIN_MAX_TOKENS}")
        groq["min_max_tokens"] = MIN_MAX_TOKENS
    extra = groq.get("extra_body") or {}   # also covers "extra_body": null
    groq["extra_body"] = extra
    if extra.get("reasoning_effort") != REASONING_EFFORT:
        changes.append(f"groq.extra_body.reasoning_effort: {extra.get('reasoning_effort')!r} -> {REASONING_EFFORT!r}")
        extra["reasoning_effort"] = REASONING_EFFORT
    if not changes:
        changes.append("already patched; nothing to change")
    return changes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join("Data", "config.json"))
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)
    changes = patch(cfg)
    for c in changes:
        sys.stdout.write(f"  {c}\n")
    if not args.apply:
        sys.stdout.write("Dry run. Re-run with --apply to back up and write.\n")
        return 0

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(os.path.dirname(args.config) or ".", "Backup", f"{stamp}_config_groq_1104")
    os.makedirs(backup_dir, exist_ok=True)
    backup = os.path.join(backup_dir, os.path.basename(args.config))
    shutil.copy2(args.config, backup)
    tmp = args.config + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")
    with open(tmp, encoding="utf-8") as f:
        json.load(f)  # never replace the real file with something that doesn't parse
    os.replace(tmp, args.config)
    sys.stdout.write(f"Backed up to {backup}\nWrote {args.config}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
