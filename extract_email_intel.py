"""
Structural intelligence extraction from financial emails/newsletters.

Extracts what the BUY/SELL analyzer misses:
  - Dated catalysts (hard regulatory deadlines, contract expirations)
  - Supply chain facts (physical constraints: tonnage, GW, capacity)
  - Missing symbols (upstream/downstream picks the author didn't pitch)
  - R&D topics implied by the data but not built

The rubric lives in prompts/email_intel_extraction.md and is versioned
alongside the code.
"""

import json
import os
from pathlib import Path

import ai_client

_RUBRIC_PATH = Path(__file__).resolve().parent / "prompts" / "email_intel_extraction.md"
_rubric_cache: str | None = None


def _rubric() -> str:
    global _rubric_cache
    if _rubric_cache is None:
        try:
            _rubric_cache = _rubric_path().read_text(encoding="utf-8")
        except FileNotFoundError:
            _rubric_cache = ""
    return _rubric_cache


def _rubric_path() -> Path:
    return _RUBRIC_PATH


def _parse(text: str) -> dict:
    if not text:
        return {}
    t = text.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(t)
    except Exception:
        return {}


_VERIFY_SYSTEM = (
    "You are a skeptical fact-checker. You are given a set of claims extracted from "
    "a financial email. For each claim, rate it: VERIFIED (specific, checkable, plausible), "
    "PLAUSIBLE (directionally sound but number/date not confirmed), or QUESTIONABLE "
    "(vague, contradicted by common knowledge, or typical newsletter exaggeration). "
    "Output strictly as JSON: {\"dated_catalysts\": [...statuses...], "
    "\"supply_chain_facts\": [...statuses...]} where each item is "
    "{\"claim\": \"...\", \"status\": \"VERIFIED|PLAUSIBLE|QUESTIONABLE\", \"note\": \"...\"}. "
    "No markdown. No prose."
)


def _verify(intel: dict, provider: str) -> dict:
    """Adversarial second-pass: ask the AI to rate each extracted claim."""
    claims = {
        "dated_catalysts": [c.get("event", "") for c in intel.get("dated_catalysts", [])],
        "supply_chain_facts": intel.get("supply_chain_facts", []),
    }
    if not any(claims.values()):
        return {}
    prompt = f"Claims to verify:\n{json.dumps(claims, indent=2)}"
    raw = ai_client.evaluate(_VERIFY_SYSTEM, prompt, provider=provider, max_tokens=600, temperature=0.1)
    return _parse(raw) or {}


def extract(subject: str, body: str, provider: str | None = None, verify: bool = True) -> dict:
    """Run the extraction rubric against one email, then optionally run an
    adversarial verification pass on the extracted claims.

    Returns {} when no AI provider is available. Never raises.
    Inspect 'pitch_ratio' before acting on 'tickers_mentioned';
    inspect '_verification' to see which claims survived adversarial review.
    """
    rubric = _rubric()
    if not rubric:
        return {}
    name = provider or ai_client.primary()
    if not name:
        return {}
    prompt = f"Subject: {subject}\n\nBody:\n{body}"
    raw = ai_client.evaluate(rubric, prompt, provider=name, max_tokens=800, temperature=0.1)
    result = _parse(raw)
    if not result:
        return {}
    result["_provider"] = name
    # Adversarial verification pass
    if verify:
        result["_verification"] = _verify(result, name)
    # Symbol cross-reference
    result = verify_symbols(result)
    return result


def verify_symbols(intel: dict) -> dict:
    """Cross-check extracted symbols against the Research universe and return a
    validation summary. Follows Zero-Trust: never assume a ticker is valid."""
    try:
        import data_api
        rows = data_api.read_research()["rows"]
        known = {r["symbol"]: r for r in rows}
    except Exception:
        known = {}

    missing_in_universe = []
    for m in intel.get("missing_symbols", []):
        sym = m.get("symbol", "")
        if sym and sym not in known:
            missing_in_universe.append(sym)
        elif sym:
            m["in_universe"] = True
            r = known[sym]
            m["pgr"] = r.get("pgr")
            m["combined"] = r.get("combined")

    # Also tag tickers_mentioned
    for t in intel.get("tickers_mentioned", []):
        sym = t.get("symbol", "")
        if sym in known:
            t["in_universe"] = True
        else:
            t["in_universe"] = False

    intel["_validation"] = {
        "universe_size": len(known),
        "missing_from_universe": missing_in_universe,
        "coverage": f"{len(known) - len(missing_in_universe)}/{len(known)} known symbols verified",
    }
    return intel


def report(intel: dict) -> str:
    """Human-readable summary of extraction results, suitable for the CLI."""
    if not intel:
        return "(no intel extracted — AI unavailable or empty response)"

    lines = []
    if intel.get("summary"):
        lines.append(f"Thesis: {intel['summary']}")
    lines.append(f"Pitch ratio: {intel.get('pitch_ratio', '?')}/10  Confidence: {intel.get('confidence', '?')}")

    v = intel.get("_validation", {})
    if v.get("missing_from_universe"):
        lines.append(f"Not in our 506-symbol universe: {', '.join(v['missing_from_universe'])}")

    cats = intel.get("dated_catalysts", [])
    vcat = {c.get("claim", ""): c for c in intel.get("_verification", {}).get("dated_catalysts", [])}
    if cats:
        lines.append(f"\nDated catalysts ({len(cats)}):")
        for c in cats:
            verdict = vcat.get(c.get("event", ""), {})
            status = f" [{verdict.get('status','')}]" if verdict else ""
            note = f" ({verdict.get('note','')})" if verdict.get("note") else ""
            lines.append(f"  {c.get('date','?')}: {c.get('event','')}{status} — {c.get('impact','')}{note}")

    facts = intel.get("supply_chain_facts", [])
    vfacts = {c.get("claim", ""): c for c in intel.get("_verification", {}).get("supply_chain_facts", [])}
    if facts:
        lines.append(f"\nSupply chain facts ({len(facts)}):")
        for f in facts:
            verdict = vfacts.get(f, {})
            status = f" [{verdict.get('status','')}]" if verdict else ""
            lines.append(f"  • {f}{status}")

    missing = intel.get("missing_symbols", [])
    if missing:
        lines.append(f"\nMissing from watchlist ({len(missing)}):")
        for m in missing:
            lines.append(f"  {m.get('symbol','?'):6} — {m.get('reason','')}")

    tickers = intel.get("tickers_mentioned", [])
    if tickers:
        lines.append(f"\nTickers mentioned ({len(tickers)}):")
        for t in tickers:
            lines.append(f"  {t.get('symbol','?'):6} [{t.get('sentiment','?')}] {t.get('thesis','')}")

    topics = intel.get("rd_topics", [])
    if topics:
        lines.append(f"\nR&D topics implied:")
        for t in topics:
            lines.append(f"  • {t}")

    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python extract_email_intel.py <email_file.txt>  (or pipe via stdin)")
        print("File format: first line = subject, blank line, rest = body")
        sys.exit(1)

    path = sys.argv[1]
    with open(path, encoding="utf-8") as f:
        content = f.read()
    parts = content.split("\n\n", 1)
    subject = parts[0].strip()
    body = parts[1].strip() if len(parts) > 1 else ""

    intel = extract(subject, body)
    print(report(intel))
    print("\n--- raw JSON ---")
    print(json.dumps(intel, indent=2))
