"""Abstract LLM provider interface shared by the fallback chain.

Every concrete provider lives in ``src/utils/providers/`` and translates its
native API into the common ``LLMResponse`` shape. The chain (see
``src/utils/llm_chain.py``) catches ``QuotaExceededError`` to advance to the
next provider and ``ProviderError`` to also advance (both failures are
considered fallbackable). Anything else bubbles up.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class LLMResponse:
    """Common response shape from every provider. ``content`` is the raw string
    even when ``json_mode`` was requested — JSON parsing happens in the caller
    (or in ``src/utils/llm.py``'s compat wrapper)."""

    content: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


class QuotaExceededError(Exception):
    """Provider has hit a rate or token quota. The chain marks it exhausted
    for the remainder of the run and tries the next provider."""


class ProviderError(Exception):
    """Any non-quota failure (malformed request, timeout, 5xx, unparseable
    response). The chain still falls back to the next provider so a flaky
    endpoint doesn't kill the run."""


class LLMProvider(ABC):
    """Provider interface. Implementations must set ``name`` and ``model`` so
    the chain can key exhausted state and so telemetry has both."""

    name: str = ""
    model: str = ""

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
        json_mode: bool = False,
        max_tokens: int = 2000,
    ) -> LLMResponse:
        """Single non-streaming chat completion.

        Raise ``QuotaExceededError`` on rate/token limits, ``ProviderError`` on
        everything else that the chain should treat as a fallback trigger.
        """
        ...
