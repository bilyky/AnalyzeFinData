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

import os
import subprocess
import sys
import tempfile

import requests
from aether.config import CFG

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
    enabled = enabled_providers()
    if CFG.ai_primary and CFG.ai_primary in enabled:
        return CFG.ai_primary
    return enabled[0] if enabled else None


def _parse_openai_response(resp) -> str:
    """Robust, type-safe parsing of OpenAI-compatible chat completion responses."""
    try:
        data = resp.json()
    except Exception as e:
        raise ValueError(f"Failed to decode response JSON: {e}") from e

    if not isinstance(data, dict):
        raise ValueError(f"Unexpected JSON response (not a dict): {data}")

    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError(f"Unexpected JSON response: missing or empty 'choices': {data}")

    choice = choices[0]
    if not isinstance(choice, dict):
        raise ValueError(f"Unexpected JSON response: 'choices[0]' is not a dict: {data}")

    message = choice.get("message")
    if not isinstance(message, dict):
        raise ValueError(f"Unexpected JSON response: 'message' is not a dict: {data}")

    content = message.get("content")
    if not isinstance(content, str):
        raise ValueError(f"Unexpected JSON response: 'content' is not a string: {data}")

    return content.strip()


def _parse_anthropic_response(resp) -> str:
    """Robust, type-safe parsing of Anthropic API message responses."""
    try:
        data = resp.json()
    except Exception as e:
        raise ValueError(f"Failed to decode response JSON: {e}") from e

    if not isinstance(data, dict):
        raise ValueError(f"Unexpected JSON response (not a dict): {data}")

    blocks = data.get("content", [])
    if isinstance(blocks, str):
        blocks = [blocks]
    if not isinstance(blocks, list):
        return ""

    texts = []
    for b in blocks:
        if isinstance(b, dict):
            if b.get("type") == "text" and isinstance(b.get("text"), str):
                texts.append(b.get("text", ""))
        elif isinstance(b, str):
            texts.append(b)
    return "".join(texts).strip()


# ── Per-type transports ────────────────────────────────────────────────────────
# Each takes a pre-built `messages` list so evaluate() (single system+user turn)
# and chat() (full history) share ONE wire path per provider — no duplicated POST.

def _require_key(pcfg, name) -> str:
    key = _resolve_key(pcfg.get("api_key_source", ""))
    if not key:
        raise RuntimeError(f"API key missing for provider '{name}'.")
    return key


def _call_openai_compatible(pcfg, key, messages, max_tokens, temperature) -> str:
    resp = requests.post(
        pcfg.get("endpoint", ""),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": pcfg.get("model", ""),
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        },
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return _parse_openai_response(resp)


def _call_anthropic(pcfg, key, system, messages, max_tokens) -> str:
    # Note: temperature is intentionally omitted — recent Claude models (Opus 4.7+)
    # reject it. If the anthropic SDK is later added, swap this raw call for it.
    # `system` is sent out-of-band (Anthropic has no system role in `messages`).
    resp = requests.post(
        _ANTHROPIC_URL,
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={
            "model": pcfg.get("model", "claude-opus-4-8"),
            "max_tokens": max_tokens,
            **({"system": system} if system else {}),
            "messages": messages,
        },
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return _parse_anthropic_response(resp)


def _run_gemini(model: str, context: str, instruction: str) -> str:
    """Single invocation path for the gemini CLI, shared by evaluate() and chat().

    Bulk `context` is fed on stdin (gemini prepends stdin to the -p text), which
    keeps large financial payloads off the Windows command line; `instruction` is
    the trailing -p directive. Workspace-trust is scoped to the child env rather
    than mutating this process's os.environ. Not exercisable where the gemini CLI
    is absent — advisory callers must degrade to deterministic behavior on any
    RuntimeError raised here."""
    out = subprocess.run(
        ["gemini", "--skip-trust", "-m", model, "-p", instruction],
        input=context,
        capture_output=True, text=True, timeout=_TIMEOUT,
        shell=(sys.platform == "win32"),
        cwd=tempfile.gettempdir(),
        encoding="utf-8",
        env={**os.environ, "GEMINI_CLI_TRUST_WORKSPACE": "true"},
    )
    if out.returncode != 0:
        raise RuntimeError(f"Gemini CLI execution failed (exit code {out.returncode}): {out.stderr.strip()}")
    return out.stdout.strip()


def _call_gemini_cli(pcfg, system, user, max_tokens, temperature) -> str:
    return _run_gemini(pcfg.get("model", "gemini-2.5-flash"),
                       f"{system}\n\n{user}",
                       "Please analyze the above data and respond:")


# ── Public entry point ─────────────────────────────────────────────────────────

def chat(messages: list, system: str = "", provider: str | None = None,
         max_tokens: int = 1000, temperature: float = 0.5) -> str:
    """Multi-turn chat: `messages` is [{role: user|assistant, content: str}].
    The last message must be role=user. Returns the assistant reply. Raises on failure."""
    name = provider or primary()
    if not name:
        raise RuntimeError("No primary AI provider configured.")
    pcfg = _providers().get(name)
    if not pcfg or not pcfg.get("enabled"):
        raise RuntimeError(f"AI provider '{name}' is disabled or not found.")
    ptype = pcfg.get("type")

    if ptype == "openai_compatible":
        payload_msgs = ([{"role": "system", "content": system}] if system else []) + messages
        return _call_openai_compatible(pcfg, _require_key(pcfg, name),
                                       payload_msgs, max_tokens, temperature)
    elif ptype == "anthropic":
        return _call_anthropic(pcfg, _require_key(pcfg, name), system, messages, max_tokens)
    elif ptype == "gemini_cli":
        # Gemini CLI: flatten history into a single stdin payload.
        turns = "\n\n".join(f"[{m['role'].upper()}]: {m['content']}" for m in messages)
        context = (f"{system}\n\n{turns}" if system else turns)
        return _run_gemini(pcfg.get("model", "gemini-2.5-flash"),
                           context,
                           "Please analyze the above conversation history and respond:")
    else:
        raise NotImplementedError(f"Unsupported AI provider type: {ptype}")


def evaluate(system: str, user: str, provider: str | None = None,
             max_tokens: int = 200, temperature: float = 0.3) -> str:
    """Run one advisory evaluation on `provider` (defaults to primary()).
    Returns the model's text. Raises on failure."""
    name = provider or primary()
    if not name:
        raise RuntimeError("No primary AI provider configured.")
    pcfg = _providers().get(name)
    if not pcfg or not pcfg.get("enabled"):
        raise RuntimeError(f"AI provider '{name}' is disabled or not found.")
    ptype = pcfg.get("type")

    if ptype == "openai_compatible":
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        return _call_openai_compatible(pcfg, _require_key(pcfg, name),
                                       messages, max_tokens, temperature)
    elif ptype == "anthropic":
        return _call_anthropic(pcfg, _require_key(pcfg, name), system,
                               [{"role": "user", "content": user}], max_tokens)
    elif ptype == "gemini_cli":
        return _call_gemini_cli(pcfg, system, user, max_tokens, temperature)
    else:
        raise NotImplementedError(f"Unsupported AI provider type: {ptype}")
