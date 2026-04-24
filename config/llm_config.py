"""Builds the default LLM fallback chain from environment variables.

Order matters — best quality / smallest quota pool first, largest quota last
so the chain holds onto capacity for the tail of a run. Each provider is
best-effort: if it fails to construct (e.g. SDK not installed), we log a
warning and move on.
"""
from __future__ import annotations

import os

from src.utils.llm_chain import LLMFallbackChain
from src.utils.llm_providers import LLMProvider
from src.utils.logger import get_logger

log = get_logger(__name__)


def build_default_chain() -> LLMFallbackChain:
    """Assemble the production fallback chain from env-var presence.

    Raises ``RuntimeError`` if no provider can be constructed — the pipeline
    needs at least one LLM backend to run scoring / writing.
    """
    providers: list[LLMProvider] = []

    if os.environ.get("GEMINI_API_KEY", "").strip():
        try:
            from src.utils.providers.gemini_provider import GeminiProvider

            providers.append(GeminiProvider(model="gemini-2.5-flash"))
        except Exception as e:  # noqa: BLE001
            log.warning("llm_config.gemini_init_failed", error=str(e))

    if os.environ.get("GROQ_API_KEY", "").strip():
        try:
            from src.utils.providers.groq_provider import GroqProvider

            providers.append(GroqProvider(model="llama-3.3-70b-versatile"))
            providers.append(GroqProvider(model="llama-3.1-8b-instant"))
            # gemma2-9b-it removed 2026-04-23: Groq decommissioned it and the
            # endpoint returns 400 on every call.
        except Exception as e:  # noqa: BLE001
            log.warning("llm_config.groq_init_failed", error=str(e))

    if os.environ.get("OPENROUTER_API_KEY", "").strip():
        try:
            from src.utils.providers.openrouter_provider import OpenRouterProvider

            # google/gemini-2.0-flash-exp:free removed 2026-04-23: OpenRouter
            # returns 404 "No endpoints found" for this user.
            providers.append(
                OpenRouterProvider(model="meta-llama/llama-3.3-70b-instruct:free")
            )
        except Exception as e:  # noqa: BLE001
            log.warning("llm_config.openrouter_init_failed", error=str(e))

    if not providers:
        raise RuntimeError(
            "No LLM providers configured. Set at least GEMINI_API_KEY or GROQ_API_KEY."
        )

    log.info(
        "llm_config.chain_ready",
        providers=[f"{p.name}:{p.model}" for p in providers],
    )
    return LLMFallbackChain(providers)
