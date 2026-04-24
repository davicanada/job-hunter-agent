"""Groq provider — thin wrapper around the async Groq SDK.

One ``GroqProvider`` instance per (api_key, model) pair. The chain instantiates
three of these by default (llama-3.3-70b-versatile, llama-3.1-8b-instant,
gemma2-9b-it) to take advantage of per-model daily token pools.
"""
from __future__ import annotations

import os
import time

from groq import APIError, AsyncGroq, RateLimitError

from src.utils.llm_providers import (
    LLMProvider,
    LLMResponse,
    ProviderError,
    QuotaExceededError,
)
from src.utils.logger import get_logger

log = get_logger(__name__)


class GroqProvider(LLMProvider):
    name = "groq"

    def __init__(self, model: str, api_key: str | None = None) -> None:
        self.model = model
        key = api_key or os.environ.get("GROQ_API_KEY", "").strip()
        if not key:
            raise RuntimeError("GROQ_API_KEY not set — cannot construct GroqProvider")
        # Chain handles fallback on quota; SDK-level retry is 0 to avoid hiding
        # the 429 from us. When the chain picks this provider again on a later
        # run (different process), retries will be fresh.
        self._client = AsyncGroq(api_key=key, max_retries=0)

    async def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
        json_mode: bool = False,
        max_tokens: int = 2000,
    ) -> LLMResponse:
        kwargs: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        start = time.perf_counter()
        try:
            resp = await self._client.chat.completions.create(**kwargs)
        except RateLimitError as e:
            raise QuotaExceededError(str(e)) from e
        except APIError as e:
            # token_limit_exceeded surfaces as APIError with status_code=429
            # on some SDK versions, so check it here too.
            status = getattr(e, "status_code", None)
            if status == 429:
                raise QuotaExceededError(str(e)) from e
            raise ProviderError(f"groq APIError: {e}") from e
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"groq unexpected: {e}") from e

        latency_ms = int((time.perf_counter() - start) * 1000)
        choices = resp.choices or []
        if not choices:
            raise ProviderError("groq returned no choices")
        content = (choices[0].message.content or "").strip()
        usage = getattr(resp, "usage", None)
        return LLMResponse(
            content=content,
            provider=self.name,
            model=self.model,
            input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            latency_ms=latency_ms,
        )
