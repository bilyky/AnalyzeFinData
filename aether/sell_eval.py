"""
Advisory AI layer for exit decisions (Part E).

Runs the versioned rubric at prompts/sell_evaluation.md through the configured
ai_client to produce a {verdict, note} second opinion on a deterministic exit
decision. Purely advisory — it annotates, never gates a trade. Degrades to an
empty verdict when no AI provider is available.

Keep this separate from sell_rules.py, which stays pure/deterministic.
"""

import json
import os

from aether import ai_client

_RUBRIC_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "prompts", "sell_evaluation.md")
_VALID = {"AGREE", "FLAG-FOR-REVIEW", "NO-OPINION"}
_rubric_cache = None


def _rubric() -> str:
    global _rubric_cache
    if _rubric_cache is None:
        try:
            with open(_RUBRIC_PATH, encoding="utf-8") as f:
                _rubric_cache = f.read()
        except FileNotFoundError:
            _rubric_cache = ""
    return _rubric_cache


def _fmt(v, nd=2):
    try:
        return f"{float(v):.{nd}f}"
    except (TypeError, ValueError):
        return "n/a"


def build_user_prompt(ctx: dict) -> str:
    """Render a position's numbers + the engine's decision for the rubric."""
    return (
        f"symbol: {ctx.get('symbol')}\n"
        f"engine_action: {ctx.get('action')}\n"
        f"engine_reason: {ctx.get('reason')}\n"
        f"entry: {_fmt(ctx.get('cost'))}\n"
        f"current_price: {_fmt(ctx.get('price'))}\n"
        f"pnl_pct: {_fmt(ctx.get('pnl_pct'), 1)}\n"
        f"s10: {ctx.get('s10')}\n"
        f"l60: {ctx.get('l60')}\n"
        f"combined: {_fmt((ctx.get('s10') or 0) + (ctx.get('l60') or 0), 1)}\n"
        f"stop: {_fmt(ctx.get('stop'))}\n"
        f"sma50: {_fmt(ctx.get('sma50'))}\n"
        f"pgr: {ctx.get('pgr')}\n"
        f"patterns: {ctx.get('patterns')}\n"
    )


def _parse(text: str) -> dict:
    """Parse the model's JSON verdict; tolerate markdown fences and stray prose."""
    if not text:
        return {}
    t = text.replace("```json", "").replace("```", "").strip()
    try:
        obj = json.loads(t)
    except Exception:
        # Best-effort: pull a known verdict token out of free text.
        upper = t.upper()
        for v in ("FLAG-FOR-REVIEW", "NO-OPINION", "AGREE"):
            if v in upper:
                return {"verdict": v, "note": t[:160]}
        return {}
    verdict = str(obj.get("verdict", "")).upper().strip()
    if verdict not in _VALID:
        return {}
    return {"verdict": verdict, "note": str(obj.get("note", "")).strip()}


def evaluate_exit(ctx: dict, provider: str | None = None) -> dict:
    """Return {'verdict': ..., 'note': ..., 'provider': name} or {} if unavailable.
    Advisory; never raises."""
    rubric = _rubric()
    if not rubric:
        return {}
    name = provider or ai_client.primary()
    if not name:
        return {}
    text = ai_client.evaluate(rubric, build_user_prompt(ctx), provider=name, max_tokens=120)
    parsed = _parse(text)
    if parsed:
        parsed["provider"] = name
    return parsed
