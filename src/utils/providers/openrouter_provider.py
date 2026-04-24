"""OpenRouter provider — OpenAI-compatible chat completions via raw HTTP.

OpenRouter aggregates many models behind one API surface; we mostly use it to
reach free-tier Gemini/Llama models that Groq or Google don't serve directly.
Quota errors come back as HTTP 429 or 402 depending on the reason.
"""
from __future__ import annotations

import os
import time

import httpx

from src.utils.llm_providers import (
    LLMProvider,
    LLMResponse,
    ProviderError,
    QuotaExceededError,
)
from src.utils.logger import get_logger

log = get_logger(__name__)

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterProvider(LLMProvider):
    name = "openrouter"

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        timeout: float = 30.0,
        referer: str = "https://github.com/davi-almeida/job-hunter-agent",
        app_title: str = "Job Hunter Agent",
    ) -> None:
        self.model = model
        self._timeout = timeout
        self._referer = referer
        self._app_title = app_title
        key = api_key or os.environ.get("OPENROUTER_API_KEY", "").strip()
        if not key:
            raise RuntimeError(
                "OPENROUTER_API_KEY not set — cannot construct OpenRouterProvider"
            )
        self._key = key

    async def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
        json_mode: bool = False,
        max_tokens: int = 2000,
    ) -> LLMResponse:
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
            "HTTP-Referer": self._referer,
            "X-Title": self._app_title,
        }

        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(_OPENROUTER_URL, json=payload, headers=headers)
        except httpx.HTTPError as e:
            raise ProviderError(f"openrouter network: {e}") from e
        latency_ms = int((time.perf_counter() - start) * 1000)

        if resp.status_code in (402, 429):
            raise QuotaExceededError(
                f"openrouter status={resp.status_code}: {resp.text[:300]}"
            )
        if resp.status_code >= 400:
            raise ProviderError(
                f"openrouter status={resp.status_code}: {resp.text[:300]}"
            )

        try:
            data = resp.json()
        except ValueError as e:
            raise ProviderError(f"openrouter non-JSON response: {e}") from e

        # Some free-tier routes return {"error": {...}} with 200 status
        err = data.get("error") if isinstance(data, dict) else None
        if err:
            msg = str(err.get("message") or err).lower()
            code = err.get("code")
            if code in (402, 429) or any(
                m in msg for m in ("rate limit", "quota", "exhaust")
            ):
                raise QuotaExceededError(f"openrouter body error: {err}")
            raise ProviderError(f"openrouter body error: {err}")

        choices = data.get("choices") or []
        if not choices:
            raise ProviderError(f"openrouter empty choices: {data}")
        content = (choices[0].get("message", {}).get("content") or "").strip()
        usage = data.get("usage") or {}
        return LLMResponse(
            content=content,
            provider=self.name,
            model=self.model,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            latency_ms=latency_ms,
        )
