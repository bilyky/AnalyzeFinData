"""
Configurable, provider-agnostic AI client for ADVISORY evaluations.

Config (`config.json` -> `ai`):
    providers  — named provider blocks, each independently enabled. Every block has a
                 `type` (the wire format, a key of TRANSPORTS), a `model`, and for HTTP
                 types an `endpoint` and `api_key_source` (`env:VAR` or a literal key).
                 Optional `extra_body` adds provider-specific request fields; the core
                 fields (model, messages, max_tokens, ...) always win.
    tiers      — named, ordered lists of provider names, e.g.
                 {"cheap": ["groq", "gemini_lite"], "strong": ["groq", "gemini_oai"]}.
    primary    — the default chain head when a call names no tier (legacy).

Transports (TRANSPORTS):
    openai_compatible  — POST {endpoint}, Bearer key (OpenRouter, Groq, Together, Fireworks,
                         Mistral, Azure OpenAI v1, Bedrock's OpenAI endpoint, Gemini ...)
    anthropic          — POST https://api.anthropic.com/v1/messages
    gemini_cli         — shell out to the `gemini` CLI

Routing (provider_chain): an explicit provider= is the whole chain. Otherwise the chain is
the tier's providers, then the "strong" tier's (escalation), then primary, then every other
enabled provider — deduped, and only providers that are enabled and have a key. A call with
no tier keeps the old order (primary, then the other enabled providers).

Advisory only: evaluate()/chat() RAISE when every provider in the chain fails (missing key,
disabled provider, HTTP error, empty answer). Callers MUST catch that and degrade to
deterministic behavior. This module never gates a trade.

Every success is logged ("AI call ok", with tier, provider, model, ms, in/out chars) and
every failure as a warning, so an outage shows in aether.jsonl the same day.
AETHER_AI_CAPTURE=<file> appends each request (system, messages, tier, limits) to a JSONL
file for scripts/diagnostics/ai_provider_bakeoff.py. Off unless the variable is set.
"""

import datetime
import json
import os
import subprocess
import sys
import tempfile
import time

import requests
from aether_logger import get_logger as _get_logger
from aether.config import CFG

_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_TIMEOUT = 180  # generous for chat; context build can take ~9s on cold Research cache
# Free OpenRouter routes answer 429 when their shared pool is busy. Retry once, waiting for
# Retry-After when it is sent, but never longer than this.
_RETRY_429_DEFAULT_WAIT_S = 3.0
_RETRY_429_MAX_WAIT_S = 10.0
# The tier a cheaper tier escalates to when all of its providers fail.
_ESCALATION_TIER = "strong"
_CAPTURE_ENV = "AETHER_AI_CAPTURE"


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


def _tiers() -> dict:
    """Tier lists from config. `_note`-style keys and non-list values are not tiers."""
    raw = getattr(CFG, "ai_tiers", None) or {}
    return {t: names for t, names in raw.items()
            if not str(t).startswith("_") and isinstance(names, list)}


def configured_providers() -> dict:
    """The provider blocks from config, by name (read-only view for diagnostics)."""
    return dict(_providers())


def configured_tiers() -> dict:
    """The tier lists from config, by tier name (read-only view for diagnostics)."""
    return {t: list(names) for t, names in _tiers().items()}


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
    """The default chain head: the configured `primary` if it's usable, else the first
    enabled provider, else None."""
    enabled = enabled_providers()
    if CFG.ai_primary and CFG.ai_primary in enabled:
        return CFG.ai_primary
    return enabled[0] if enabled else None


def provider_chain(provider: str | None = None, tier: str | None = None) -> list:
    """The ordered provider names a call will try (see the module docstring)."""
    if provider:
        return [provider]
    usable = enabled_providers()
    tiers = _tiers()
    order = []
    if tier:
        for t in (tier, _ESCALATION_TIER):
            order += [n for n in tiers.get(t, []) if n in usable]
    head = primary()
    if head:
        order.append(head)
    order += usable
    return list(dict.fromkeys(order))


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
    if content is None and choice.get("finish_reason") == "length":
        raise ValueError("Model ran out of tokens before answering (finish_reason=length, content empty). "
                         "Reasoning models spend max_tokens on reasoning first; set the provider's "
                         "extra_body to {\"reasoning\": {\"enabled\": false}} or pick a non-reasoning model.")
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


# ── Transports ─────────────────────────────────────────────────────────────────
# One function per wire format, all with the same signature:
#     transport(pcfg, name, system, messages, max_tokens, temperature) -> str
# `messages` is [{role: user|assistant, content}]; a single user message is an
# evaluate() call, a longer list is chat(). Add a wire format by adding an entry.

def _require_key(pcfg, name) -> str:
    key = _resolve_key(pcfg.get("api_key_source", ""))
    if not key:
        raise RuntimeError(f"API key missing for provider '{name}'.")
    return key


def _require_model(pcfg, name) -> str:
    model = pcfg.get("model", "")
    if not model:
        raise RuntimeError(f"Provider '{name}' has no model configured (set its 'model' in config.json).")
    return model


def _t_openai_compatible(pcfg, name, system, messages, max_tokens, temperature) -> str:
    model = _require_model(pcfg, name)
    key = _require_key(pcfg, name)
    # extra_body: provider-specific request fields from config (e.g. OpenRouter's
    # {"reasoning": {"enabled": false}}). The core fields below always win.
    payload = dict(pcfg.get("extra_body") or {})
    payload.update({
        "model": model,
        "messages": ([{"role": "system", "content": system}] if system else []) + messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    })
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    for attempt in (1, 2):
        resp = requests.post(pcfg.get("endpoint", ""), headers=headers, json=payload, timeout=_TIMEOUT)
        try:
            resp.raise_for_status()
        except requests.HTTPError:
            if attempt == 2 or getattr(resp, "status_code", None) != 429:
                raise
            try:
                wait = float(resp.headers.get("Retry-After", _RETRY_429_DEFAULT_WAIT_S))
            except (TypeError, ValueError):
                wait = _RETRY_429_DEFAULT_WAIT_S
            wait = min(max(wait, 0.0), _RETRY_429_MAX_WAIT_S)
            _get_logger("ai_client").warning(f"AI provider returned 429; retrying once in {wait:.0f}s.")
            time.sleep(wait)
            continue
        return _parse_openai_response(resp)


def _t_anthropic(pcfg, name, system, messages, max_tokens, temperature) -> str:
    # temperature is intentionally omitted: recent Claude models reject it.
    # `system` is sent out-of-band (Anthropic has no system role in `messages`).
    model = _require_model(pcfg, name)
    key = _require_key(pcfg, name)
    payload = dict(pcfg.get("extra_body") or {})
    payload.update({"model": model, "max_tokens": max_tokens, "messages": messages})
    if system:
        payload["system"] = system
    resp = requests.post(
        _ANTHROPIC_URL,
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json=payload,
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return _parse_anthropic_response(resp)


def _run_gemini(model: str, context: str, instruction: str) -> str:
    """Single invocation path for the gemini CLI.

    Bulk `context` is fed on stdin (gemini prepends stdin to the -p text), which
    keeps large financial payloads off the Windows command line; `instruction` is
    the trailing -p directive. Workspace-trust is scoped to the child env rather
    than mutating this process's os.environ. `--allowed-tools none` is what keeps
    the CLI from running tools under `--approval-mode yolo`; keep it until it is
    replaced by an equivalent policy setting."""
    # Suppress true color terminal warnings in headless environments
    env = os.environ.copy()
    env["COLORTERM"] = "truecolor"
    env["GEMINI_CLI_TRUST_WORKSPACE"] = "true"

    executable = "gemini.cmd" if sys.platform == "win32" else "gemini"
    args = [executable, "--skip-trust", "-m", model, "--approval-mode", "yolo", "--allowed-mcp-server-names", "none", "--allowed-tools", "none", "-p", instruction]

    with tempfile.TemporaryDirectory(prefix="aether_gem_") as sandbox:
        out = subprocess.run(
            args,
            input=context,
            capture_output=True, text=True, timeout=_TIMEOUT,
            shell=(sys.platform == "win32"),
            cwd=sandbox,
            encoding="utf-8",
            env=env,
        )

    if out.returncode != 0:
        err_msg = ""
        if out.stderr.strip():
            err_msg += out.stderr.strip()
        if out.stdout.strip():
            if err_msg:
                err_msg += "\n"
            err_msg += out.stdout.strip()
        raise RuntimeError(f"Gemini CLI execution failed (exit code {out.returncode}): {err_msg}")
    return out.stdout.strip()


def _t_gemini_cli(pcfg, name, system, messages, max_tokens, temperature) -> str:
    model = pcfg.get("model", "gemini-2.5-flash")
    if len(messages) == 1 and messages[0].get("role") == "user":
        return _run_gemini(model, f"{system}\n\n{messages[0]['content']}",
                           "Please analyze the following data and respond. Do NOT use any tools. Rely ONLY on the text below:")
    # Chat: flatten history into a single stdin payload.
    turns = "\n\n".join(f"[{m['role'].upper()}]: {m['content']}" for m in messages)
    context = (f"{system}\n\n{turns}" if system else turns)
    return _run_gemini(model, context,
                       "Please analyze the following conversation history and respond. Do NOT use any tools. Rely ONLY on the text below:")


TRANSPORTS = {
    "openai_compatible": _t_openai_compatible,
    "anthropic": _t_anthropic,
    "gemini_cli": _t_gemini_cli,
}


# ── Dispatch ───────────────────────────────────────────────────────────────────

def _call_one(name, system, messages, max_tokens, temperature) -> str:
    pcfg = _providers().get(name)
    if not pcfg or not pcfg.get("enabled"):
        raise RuntimeError(f"AI provider '{name}' is disabled or not found.")
    transport = TRANSPORTS.get(pcfg.get("type"))
    if transport is None:
        raise NotImplementedError(f"Unsupported AI provider type: {pcfg.get('type')}")
    text = transport(pcfg, name, system, messages, max_tokens, temperature)
    if not (text or "").strip():
        raise ValueError(f"AI provider '{name}' returned an empty answer.")
    return text


def _capture(system, messages, tier, max_tokens, temperature):
    path = os.environ.get(_CAPTURE_ENV)
    if not path:
        return
    row = {"ts": datetime.datetime.now().isoformat(timespec="seconds"), "tier": tier,
           "system": system, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as e:
        _get_logger("ai_client").warning(f"AI prompt capture to {path} failed: {e}")


def _run(kind, system, messages, provider, tier, max_tokens, temperature) -> tuple[str, str]:
    """Try each provider in the chain; return (text, name of the provider that answered)."""
    chain = provider_chain(provider, tier)
    if not chain:
        raise RuntimeError("No primary AI provider configured.")
    _capture(system, messages, tier, max_tokens, temperature)
    log = _get_logger("ai_client")
    in_chars = len(system or "") + sum(len(m.get("content") or "") for m in messages)
    last_err = None
    for name in chain:
        start = time.monotonic()
        try:
            text = _call_one(name, system, messages, max_tokens, temperature)
        except Exception as e:
            log.warning(f"AI {kind} failed for provider '{name}'; attempting fallback...",
                        extra={"provider": name, "ai_tier": tier, "error": str(e)})
            last_err = e
            continue
        log.info(f"AI call ok: {name}",
                 extra={"ai_kind": kind, "ai_tier": tier, "ai_provider": name,
                        "ai_model": (_providers().get(name) or {}).get("model", ""),
                        "ms": int((time.monotonic() - start) * 1000),
                        "in_chars": in_chars, "out_chars": len(text)})
        return text, name
    raise last_err


# ── Public entry points ────────────────────────────────────────────────────────

def chat_with_provider(messages: list, system: str = "", provider: str | None = None,
                       max_tokens: int = 1000, temperature: float = 0.5,
                       tier: str | None = None) -> tuple[str, str]:
    """Multi-turn chat: `messages` is [{role: user|assistant, content: str}], the last one
    role=user. Returns (reply, name of the provider that answered). Raises on failure."""
    return _run("chat", system, messages, provider, tier, max_tokens, temperature)


def chat(messages: list, system: str = "", provider: str | None = None,
         max_tokens: int = 1000, temperature: float = 0.5, tier: str | None = None) -> str:
    """Like chat_with_provider(), returning only the reply."""
    return chat_with_provider(messages, system, provider, max_tokens, temperature, tier)[0]


def evaluate_with_provider(system: str, user: str, provider: str | None = None,
                           max_tokens: int = 200, temperature: float = 0.3,
                           tier: str | None = None) -> tuple[str, str]:
    """One advisory evaluation. Returns (text, name of the provider that answered).
    Raises on failure."""
    return _run("evaluation", system, [{"role": "user", "content": user}],
                provider, tier, max_tokens, temperature)


def evaluate(system: str, user: str, provider: str | None = None,
             max_tokens: int = 200, temperature: float = 0.3, tier: str | None = None) -> str:
    """Like evaluate_with_provider(), returning only the text."""
    return evaluate_with_provider(system, user, provider, max_tokens, temperature, tier)[0]
