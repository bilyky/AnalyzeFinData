"""Patch the `ai` block of Data/config.json 2026-10-20: replace the two free OpenRouter models (the fallback was removed from OpenRouter; the primary returns 404).

Run from the repo root on PROD, any time (config only, no deploy needed):
    venv_new\\Scripts\\python.exe docs\\prod-notes\\patch_ai_models_2026_10_20.py            # dry run: prints the plan
    venv_new\\Scripts\\python.exe docs\\prod-notes\\patch_ai_models_2026_10_20.py --apply    # backs up, then writes

What it changes (nothing else in the file):
- ai.primary -> "openrouter"
- ai.providers.openrouter: enabled, endpoint fixed to openrouter.ai, model, extra_body (reasoning off).
  Its api_key_source is KEPT as it is.
- ai.providers.openrouter_fb: added (or updated) with the same api_key_source as openrouter.
- ai.providers.github_gpt.enabled -> false (retired 2026-07-30), if present.
It never prints a key. The backup goes to Data/Backup/<timestamp>_config_ai_models_1020/config.json.
"""
import argparse
import datetime
import json
import os
import shutil
import sys

ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
PRIMARY_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
FALLBACK_MODEL = "poolside/laguna-s-2.1:free"
EXTRA_BODY = {"reasoning": {"enabled": False}}


def patch(cfg: dict) -> list[str]:
    """Apply the change to cfg in place and return a list of human-readable changes."""
    ai = cfg.setdefault("ai", {})
    providers = ai.setdefault("providers", {})
    orr = providers.get("openrouter")
    if not orr or not orr.get("api_key_source"):
        raise SystemExit("ai.providers.openrouter or its api_key_source is missing; fix that by hand first.")
    changes = []

    def setv(d, key, val, label):
        if d.get(key) != val:
            changes.append(f"{label}: {d.get(key)!r} -> {val!r}")
            d[key] = val

    setv(ai, "primary", "openrouter", "ai.primary")
    setv(orr, "enabled", True, "openrouter.enabled")
    setv(orr, "type", "openai_compatible", "openrouter.type")
    setv(orr, "endpoint", ENDPOINT, "openrouter.endpoint")
    setv(orr, "model", PRIMARY_MODEL, "openrouter.model")
    setv(orr, "extra_body", EXTRA_BODY, "openrouter.extra_body")

    fb = providers.setdefault("openrouter_fb", {})
    if not fb:
        changes.append("openrouter_fb: added")
    setv(fb, "enabled", True, "openrouter_fb.enabled")
    setv(fb, "type", "openai_compatible", "openrouter_fb.type")
    setv(fb, "endpoint", ENDPOINT, "openrouter_fb.endpoint")
    setv(fb, "model", FALLBACK_MODEL, "openrouter_fb.model")
    if fb.get("api_key_source") != orr["api_key_source"]:
        fb["api_key_source"] = orr["api_key_source"]
        changes.append("openrouter_fb.api_key_source: set to the same value as openrouter (not shown)")
    setv(fb, "extra_body", EXTRA_BODY, "openrouter_fb.extra_body")

    if "github_gpt" in providers:
        setv(providers["github_gpt"], "enabled", False, "github_gpt.enabled")

    enabled = [n for n, p in providers.items() if p.get("enabled")]
    changes.append(f"enabled providers after the change (the fallback chain): {enabled}")
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
    backup_dir = os.path.join(os.path.dirname(args.config) or ".", "Backup", f"{stamp}_config_ai_models_1020")
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
