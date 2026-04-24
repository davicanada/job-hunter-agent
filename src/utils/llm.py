"""LLM entry point — thin async wrapper over the provider fallback chain.

This module intentionally stays tiny. All the real work (provider logic, retry,
telemetry) lives in ``src/utils/llm_chain.py`` + ``src/utils/providers/``.
Callers that only need the content string use ``await chat(...)``; callers
that need provider/model/token metadata (e.g. the scorer, which records the
model into ``scored_jobs.model``) use ``await chat_with_meta(...)`` or reach
``get_chain()`` directly.
"""
from __future__ import annotations

import json
from typing import Any

from src.utils.llm_chain import LLMFallbackChain
from src.utils.llm_providers import LLMResponse
from src.utils.logger import get_logger

log = get_logger(__name__)

_chain: LLMFallbackChain | None = None


def get_chain() -> LLMFallbackChain:
    """Return the process-wide ``LLMFallbackChain`` (built lazily on first call)."""
    global _chain
    if _chain is None:
        from config.llm_config import build_default_chain

        _chain = build_default_chain()
    return _chain


def set_chain(chain: LLMFallbackChain | None) -> None:
    """Override the cached chain (tests only)."""
    global _chain
    _chain = chain


async def chat(
    messages: list[dict[str, str]],
    temperature: float = 0.3,
    json_mode: bool = False,
    max_tokens: int = 2000,
) -> Any:
    """Return just the content. Parses JSON when ``json_mode`` is set (matches
    the pre-3.5 sync wrapper's contract so existing callers stay compatible
    aside from the ``await``)."""
    resp = await get_chain().chat(
        messages,
        temperature=temperature,
        json_mode=json_mode,
        max_tokens=max_tokens,
    )
    if json_mode:
        try:
            return json.loads(resp.content)
        except json.JSONDecodeError as e:
            log.error(
                "llm.json_parse.failed",
                provider=resp.provider,
                model=resp.model,
                error=str(e),
                snippet=resp.content[:500],
            )
            raise
    return resp.content


async def chat_with_meta(
    messages: list[dict[str, str]],
    temperature: float = 0.3,
    json_mode: bool = False,
    max_tokens: int = 2000,
) -> LLMResponse:
    """Return the raw ``LLMResponse`` (provider, model, token counts). The
    scorer uses this to stamp ``scored_jobs.model`` with the actual backend
    that answered."""
    return await get_chain().chat(
        messages,
        temperature=temperature,
        json_mode=json_mode,
        max_tokens=max_tokens,
    )


def get_chain_status() -> dict[str, str]:
    """Return the in-process availability of every provider in the chain.

    The returned dict is keyed on ``"<name>:<model>"`` (the same key the
    chain uses internally — keeps Groq's three models distinguishable even
    though they share ``name="groq"``). Values are:

    * ``"available"`` — not cooling down
    * ``"exhausted (retry in Ns)"`` — a ``QuotaExceededError`` triggered a
      cooldown window that has not yet elapsed

    The chain stores cooldown deadlines as ``time.monotonic()`` timestamps,
    so this function reports the *remaining* seconds rather than an absolute
    clock time — monotonic timestamps aren't meaningful across processes.
    Expired cooldowns are treated as ``"available"``.

    Does not make any network calls. Lazily builds the chain on first
    invocation — same pattern as ``chat()``.
    """
    import time as _time

    chain = get_chain()
    now = _time.monotonic()
    status: dict[str, str] = {}
    for p in chain.providers:
        key = f"{p.name}:{p.model}"
        retry_at = chain.exhausted.get(key)
        if retry_at is None or retry_at <= now:
            status[key] = "available"
        else:
            remaining = max(1, int(retry_at - now))
            status[key] = f"exhausted (retry in {remaining}s)"
    return status
