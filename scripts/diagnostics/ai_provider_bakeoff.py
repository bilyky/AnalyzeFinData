"""
AI provider bake-off — run real, captured prompts through several providers side by side,
so a provider switch is chosen on the answers, not on price alone.

1. Capture real prompts: run the normal jobs with AETHER_AI_CAPTURE set to a file, e.g.
       set AETHER_AI_CAPTURE=D:\\path\\ai_prompts.jsonl
   Every advisory AI call then appends one JSON line (tier, system, messages, max_tokens,
   temperature). The prompts hold position data: keep the file local, never commit it.
2. Enable the providers to compare in config.json (each needs its key), then run:
       python scripts/diagnostics/ai_provider_bakeoff.py --prompts ai_prompts.jsonl ^
           --providers groq,gemini_lite,together_deepseek --limit 20 --out bakeoff.md

Each prompt goes to each named provider exactly once (provider= pins it, no fallback).
Every call is a real request and may cost money on paid providers. Failures are recorded,
not raised. The report lists, per provider, how many answered, the average latency and
answer length, then every answer side by side for reading.

Writes nothing unless --out is given (then only that file).
"""
import argparse
import json
import os
import sys
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE_DIR)

from aether import ai_client


def _out(line: str = "") -> None:
    """Report line — intentional CLI output, unprefixed."""
    sys.stdout.write(line + "\n")


def load_prompts(path: str, limit: int | None = None) -> list:
    """Captured prompts from an AETHER_AI_CAPTURE JSONL file; bad lines are skipped."""
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("messages"):
                rows.append(row)
            if limit and len(rows) >= limit:
                break
    return rows


def run_bakeoff(rows: list, providers: list, call=ai_client.chat_with_provider) -> list:
    """Every prompt on every provider. One result dict per (prompt, provider)."""
    results = []
    for i, row in enumerate(rows):
        for name in providers:
            start = time.monotonic()
            try:
                text, _ = call(row["messages"], system=row.get("system", ""), provider=name,
                               max_tokens=row.get("max_tokens", 300),
                               temperature=row.get("temperature", 0.3), tier=row.get("tier"))
                ok, error = True, ""
            except Exception as e:
                text, ok, error = "", False, f"{type(e).__name__}: {e}"
            results.append({"prompt": i, "tier": row.get("tier"), "provider": name, "ok": ok,
                            "ms": int((time.monotonic() - start) * 1000),
                            "chars": len(text), "text": text, "error": error})
    return results


def summarize(results: list) -> list:
    """Per provider: answered / total, average latency and length of the answers."""
    out = []
    for name in dict.fromkeys(r["provider"] for r in results):
        mine = [r for r in results if r["provider"] == name]
        good = [r for r in mine if r["ok"]]
        out.append({"provider": name, "ok": len(good), "total": len(mine),
                    "avg_ms": int(sum(r["ms"] for r in good) / len(good)) if good else None,
                    "avg_chars": int(sum(r["chars"] for r in good) / len(good)) if good else None})
    return out


def render(rows: list, results: list) -> str:
    lines = ["# AI provider bake-off", "", "| provider | answered | avg ms | avg chars |",
             "|---|---|---|---|"]
    for s in summarize(results):
        lines.append(f"| {s['provider']} | {s['ok']}/{s['total']} | {s['avg_ms']} | {s['avg_chars']} |")
    for i, row in enumerate(rows):
        question = row["messages"][-1].get("content", "")
        lines += ["", f"## Prompt {i + 1} (tier: {row.get('tier')})", "", "> " + question[:400].replace("\n", " ")]
        for r in (r for r in results if r["prompt"] == i):
            body = r["text"] if r["ok"] else f"FAILED: {r['error']}"
            lines += ["", f"**{r['provider']}** ({r['ms']} ms)", "", body]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--prompts", required=True, help="AETHER_AI_CAPTURE JSONL file")
    ap.add_argument("--providers", required=True, help="comma-separated provider names from config.json")
    ap.add_argument("--limit", type=int, default=20, help="max prompts to run (default 20)")
    ap.add_argument("--out", help="write the full markdown report here")
    args = ap.parse_args(argv)

    names = [n.strip() for n in args.providers.split(",") if n.strip()]
    unknown = [n for n in names if n not in ai_client.configured_providers()]
    if unknown:
        _out(f"Unknown provider(s): {', '.join(unknown)}")
        return 2
    rows = load_prompts(args.prompts, args.limit)
    if not rows:
        _out(f"No prompts in {args.prompts}")
        return 2
    _out(f"Running {len(rows)} prompt(s) on {', '.join(names)} ({len(rows) * len(names)} requests)...")
    results = run_bakeoff(rows, names)
    report = render(rows, results)
    for s in summarize(results):
        _out(f"  {s['provider']:<22} answered {s['ok']}/{s['total']}  avg {s['avg_ms']} ms  avg {s['avg_chars']} chars")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report)
        _out(f"Report: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
