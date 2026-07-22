"""
Configurable multi-provider AI client for ADVISORY evaluations.

Providers are defined in config.json `ai.providers` (each independently
enable/disable-able) with one marked `primary`. Every provider has a `type`
that selects the transport:
    openai_compatible  — POST {endpoint} (GitHub Models / OpenAI / any compatible)
    anthropic          — POST https://api.anthropic.com/v1/messages
    gemini_cli         — shell out to the `gemini` CLI

Advisory only: `evaluate()` returns "" on any failure / missing key / disabled
provider, and callers MUST degrade to deterministic behavior. This module never
raises to its callers and never gates a trade.
"""

import json
import os
import subprocess

import requests

_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_TIMEOUT = 60   # generous for chat; context build can take ~9s on cold Research cache


# ── Key resolution ─────────────────────────────────────────────────────────────

def _resolve_key(source: str) -> str:
    """Resolve an api_key_source: 'env:VAR' reads os.environ then the .env file;
    anything else is treated as a literal key. Empty source -> ''."""
    if not source:
        return ""
    if source.startswith("env:"):
        var = source[4:]
        val = os.environ.get(var, "")
        if val:
            return val
        # Fall back to the project .env file (matches existing GITHUB_TOKEN pattern).
        env_path = os.path.join(_DIR, ".env")
        try:
            with open(env_path) as f:
                for line in f:
                    if line.startswith(var + "="):
                        return line.split("=", 1)[1].strip().strip('"')
        except FileNotFoundError:
            return ""
        return ""
    return source


# ── Provider registry ──────────────────────────────────────────────────────────

def _providers() -> dict:
    from config import CFG
    return CFG.ai_providers or {}


def _has_key(pcfg: dict) -> bool:
    # gemini_cli authenticates via the CLI's own login, not an api_key_source.
    if pcfg.get("type") == "gemini_cli":
        return True
    return bool(_resolve_key(pcfg.get("api_key_source", "")))


def enabled_providers() -> list:
    """Names of providers that are enabled AND have a resolvable key (or are CLI)."""
    return [name for name, p in _providers().items()
            if p.get("enabled") and _has_key(p)]


def primary() -> str | None:
    """The provider that drives the surfaced verdict: the configured `primary` if
    it's usable, else the first enabled provider, else None."""
    from config import CFG
    enabled = enabled_providers()
    if CFG.ai_primary and CFG.ai_primary in enabled:
        return CFG.ai_primary
    return enabled[0] if enabled else None


# ── Per-type transports ────────────────────────────────────────────────────────

def _call_openai_compatible(pcfg, key, system, user, max_tokens, temperature) -> str:
    resp = requests.post(
        pcfg.get("endpoint", ""),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": pcfg.get("model", ""),
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        },
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


def _call_anthropic(pcfg, key, system, user, max_tokens, temperature) -> str:
    # Note: temperature is intentionally omitted — recent Claude models (Opus 4.7+)
    # reject it. If the anthropic SDK is later added, swap this raw call for it.
    resp = requests.post(
        _ANTHROPIC_URL,
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={
            "model": pcfg.get("model", "claude-opus-4-8"),
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        },
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    blocks = resp.json().get("content", [])
    return "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()


def _call_gemini_cli(pcfg, system, user, max_tokens, temperature) -> str:
    prompt = f"{system}\n\n{user}"
    out = subprocess.run(
        ["gemini", "-m", pcfg.get("model", "gemini-2.5-flash"), "-p", prompt],
        capture_output=True, text=True, timeout=_TIMEOUT,
    )
    return out.stdout.strip() if out.returncode == 0 else ""


# ── Public entry point ─────────────────────────────────────────────────────────

def chat(messages: list, system: str = "", provider: str | None = None,
         max_tokens: int = 1000, temperature: float = 0.5) -> str:
    """Multi-turn chat: `messages` is [{role: user|assistant, content: str}].
    The last message must be role=user. Returns the assistant reply or "" on failure."""
    name = provider or primary()
    if not name:
        return ""
    pcfg = _providers().get(name)
    if not pcfg or not pcfg.get("enabled"):
        return ""
    ptype = pcfg.get("type")
    try:
        if ptype == "openai_compatible":
            key = _resolve_key(pcfg.get("api_key_source", ""))
            if not key:
                return ""
            payload_msgs = ([{"role": "system", "content": system}] if system else []) + messages
            resp = requests.post(
                pcfg.get("endpoint", ""),
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"model": pcfg.get("model", ""), "messages": payload_msgs,
                      "max_tokens": max_tokens, "temperature": temperature},
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        if ptype == "anthropic":
            key = _resolve_key(pcfg.get("api_key_source", ""))
            if not key:
                return ""
            resp = requests.post(
                _ANTHROPIC_URL,
                headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": pcfg.get("model", "claude-opus-4-8"),
                      "max_tokens": max_tokens,
                      **({"system": system} if system else {}),
                      "messages": messages},
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            blocks = resp.json().get("content", [])
            return "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
        if ptype == "gemini_cli":
            # Gemini CLI: flatten history into a single prompt
            turns = "\n\n".join(f"[{m['role'].upper()}]: {m['content']}" for m in messages)
            prompt = (f"{system}\n\n{turns}" if system else turns)
            out = subprocess.run(
                ["gemini", "-m", pcfg.get("model", "gemini-2.5-flash"), "-p", prompt],
                capture_output=True, text=True, timeout=_TIMEOUT,
            )
            return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""
    return ""


def evaluate(system: str, user: str, provider: str | None = None,
             max_tokens: int = 200, temperature: float = 0.3) -> str:
    """Run one advisory evaluation on `provider` (defaults to primary()).
    Returns the model's text, or "" on any failure / no usable provider.
    Never raises."""
    name = provider or primary()
    if not name:
        return ""
    pcfg = _providers().get(name)
    if not pcfg or not pcfg.get("enabled"):
        return ""
    ptype = pcfg.get("type")
    try:
        if ptype == "openai_compatible":
            key = _resolve_key(pcfg.get("api_key_source", ""))
            if not key:
                return ""
            return _call_openai_compatible(pcfg, key, system, user, max_tokens, temperature)
        if ptype == "anthropic":
            key = _resolve_key(pcfg.get("api_key_source", ""))
            if not key:
                return ""
            return _call_anthropic(pcfg, key, system, user, max_tokens, temperature)
        if ptype == "gemini_cli":
            return _call_gemini_cli(pcfg, system, user, max_tokens, temperature)
    except Exception:
        return ""
    return ""
