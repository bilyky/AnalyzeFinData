"""Add AI tiers and the ready provider blocks to config.json for PR #192 (R&D #53).

Run from the repo root on PROD, AFTER #192 is deployed (it reads the new config.json.example):
    venv_new\\Scripts\\python.exe docs\\prod-notes\\patch_ai_tiers_192.py --config <file>             # dry run
    venv_new\\Scripts\\python.exe docs\\prod-notes\\patch_ai_tiers_192.py --config <file> --apply     # backup + write
    ... --enable groq --enable gemini_lite --enable gemini_oai --apply
    ... --disable openrouter --disable openrouter_fb --apply

<file> is the config the app reads (step 1 of the note finds it).

What it changes in the `ai` block (nothing else in the file):
- ai.tiers: added from config.json.example if missing. An existing ai.tiers is left alone.
- Provider blocks that exist in config.json.example but not in your file are added DISABLED
  (groq, gemini_lite, together_deepseek, ...). Existing blocks are never changed except for the
  `enabled` flag you ask for, so your keys and models stay as they are.
- --enable NAME (repeatable): enabled=true, only if its key resolves (env:VAR set in the
  environment or .env, or a literal key) and it has no <placeholder> left. Otherwise it stops.
- --disable NAME (repeatable): enabled=false. If that was ai.primary, primary moves to the first
  enabled provider of the 'strong' tier.
- Before writing, every tier must still have at least one enabled provider with a key, so a
  change can never leave the AI with nothing to call. Otherwise it stops and writes nothing.
It never prints a key. The backup goes to Data/Backup/<timestamp>_config_ai192/config.json.
"""
import argparse
import copy
import datetime
import json
import os
import shutil
import sys


def _key_resolves(source: str) -> bool:
    if not source:
        return False
    if not source.startswith("env:"):
        return True  # literal key
    var = source[4:]
    if os.environ.get(var):
        return True
    try:
        with open(".env", encoding="utf-8") as f:
            return any(line.startswith(var + "=") and line.split("=", 1)[1].strip() for line in f)
    except FileNotFoundError:
        return False


def _usable(p: dict) -> bool:
    return bool(p.get("enabled")) and (p.get("type") == "gemini_cli" or _key_resolves(p.get("api_key_source", "")))


def _tiers(ai: dict) -> dict:
    return {t: v for t, v in ai.get("tiers", {}).items() if not str(t).startswith("_") and isinstance(v, list)}


def patch(cfg: dict, example: dict, enable: list, disable: list) -> list[str]:
    """Apply the change to cfg in place and return human-readable changes. Raises SystemExit
    when the result would leave a tier with no usable provider."""
    ai = cfg.setdefault("ai", {})
    providers = ai.setdefault("providers", {})
    ex_ai = example.get("ai", {})
    changes = []

    if "tiers" not in ai:
        ai["tiers"] = copy.deepcopy(ex_ai.get("tiers", {}))
        changes.append(f"ai.tiers: added {_tiers(ai)}")
    else:
        changes.append("ai.tiers: already present, left as is")

    for name, block in ex_ai.get("providers", {}).items():
        if name not in providers:
            new = copy.deepcopy(block)
            new["enabled"] = False
            providers[name] = new
            changes.append(f"providers.{name}: added (disabled), model={new.get('model')!r}")

    for name in enable:
        p = providers.get(name)
        if p is None:
            raise SystemExit(f"--enable {name}: no such provider block.")
        if p.get("type") != "gemini_cli" and not _key_resolves(p.get("api_key_source", "")):
            ref = "an env: reference" if str(p.get("api_key_source", "")).startswith("env:") else "empty"
            raise SystemExit(f"--enable {name}: its key does not resolve (api_key_source is {ref}). Set the key first.")
        if "<" in str(p.get("model", "")) or "<" in str(p.get("endpoint", "")):
            raise SystemExit(f"--enable {name}: fill in the <placeholders> in its model/endpoint first.")
        if not p.get("enabled"):
            p["enabled"] = True
            changes.append(f"providers.{name}.enabled: false -> true")

    for name in disable:
        p = providers.get(name)
        if p is None:
            raise SystemExit(f"--disable {name}: no such provider block.")
        if p.get("enabled"):
            p["enabled"] = False
            changes.append(f"providers.{name}.enabled: true -> false")

    tiers = _tiers(ai)
    if ai.get("primary") and not _usable(providers.get(ai["primary"], {})):
        new_primary = next((n for n in tiers.get("strong", []) if _usable(providers.get(n, {}))), None)
        if new_primary:
            changes.append(f"ai.primary: {ai['primary']!r} -> {new_primary!r} (old primary is not usable)")
            ai["primary"] = new_primary

    empty = []
    for t, names in tiers.items():
        live = [n for n in names if _usable(providers.get(n, {}))]
        changes.append(f"tier {t}: {names} -> usable now: {live or 'NONE'}")
        if not live:
            empty.append(t)
    if empty:
        fallback = [n for n, p in providers.items() if _usable(p)]
        changes.append(f"other usable providers the chain would fall back to: {fallback or 'NONE'}")
        if not fallback or disable:
            for c in changes:
                sys.stdout.write(f"  {c}\n")
            raise SystemExit(f"Stopped: tier(s) {empty} would have no usable provider. "
                             "Enable a provider for them (with its key set) before disabling others.")
    return changes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, help="the config.json the app reads")
    ap.add_argument("--example", default="config.json.example")
    ap.add_argument("--enable", action="append", default=[], metavar="NAME")
    ap.add_argument("--disable", action="append", default=[], metavar="NAME")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    with open(args.example, encoding="utf-8") as f:
        example = json.load(f)
    if "tiers" not in example.get("ai", {}):
        raise SystemExit(f"{args.example} has no ai.tiers: deploy PR #192 first.")
    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)

    for c in patch(cfg, example, args.enable, args.disable):
        sys.stdout.write(f"  {c}\n")
    if not args.apply:
        sys.stdout.write("Dry run. Re-run with --apply to back up and write.\n")
        return 0

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bdir = os.path.join("Data", "Backup", f"{stamp}_config_ai192")
    os.makedirs(bdir, exist_ok=True)
    shutil.copy2(args.config, os.path.join(bdir, "config.json"))
    tmp = args.config + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")
    with open(tmp, encoding="utf-8") as f:
        json.load(f)  # must still be valid JSON
    os.replace(tmp, args.config)
    sys.stdout.write(f"Written. Backup: {bdir}\\config.json\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
