"""Google Gemini provider built on the ``google-genai`` SDK.

Translates the OpenAI-style ``[{role, content}]`` messages into Gemini's
``contents`` + ``system_instruction`` split, uses ``response_mime_type`` for
JSON mode, and maps RESOURCE_EXHAUSTED / 429 errors to ``QuotaExceededError``.
"""
from __future__ import annotations

import os
import time

from src.utils.llm_providers import (
    LLMProvider,
    LLMResponse,
    ProviderError,
    QuotaExceededError,
)
from src.utils.logger import get_logger

log = get_logger(__name__)

_QUOTA_MARKERS: tuple[str, ...] = (
    "resource_exhausted",
    "rate limit",
    "rate_limit",
    "quota",
    "exhaust",
    "too many requests",
)


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, model: str = "gemini-2.5-flash", api_key: str | None = None) -> None:
        self.model = model
        key = api_key or os.environ.get("GEMINI_API_KEY", "").strip()
        if not key:
            raise RuntimeError("GEMINI_API_KEY not set — cannot construct GeminiProvider")
        try:
            from google import genai  # type: ignore
            from google.genai import types  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "google-genai package not installed. Add `google-genai` to requirements.txt."
            ) from e
        self._genai = genai
        self._types = types
        self._client = genai.Client(api_key=key)

    def _raise_typed(self, e: Exception) -> None:
        code = getattr(e, "code", None) or getattr(e, "status_code", None)
        msg = str(e).lower()
        if code in (429, "429") or any(m in msg for m in _QUOTA_MARKERS):
            raise QuotaExceededError(str(e)) from e
        raise ProviderError(f"gemini: {e}") from e

    @staticmethod
    def _split_messages(
        messages: list[dict[str, str]],
    ) -> tuple[str | None, list[dict]]:
        """Gemini takes system_instruction separately from contents.

        Returns (system_instruction_or_None, gemini_contents_list).
        """
        system_parts: list[str] = []
        contents: list[dict] = []
        for m in messages:
            role = m.get("role")
            content = m.get("content") or ""
            if role == "system":
                system_parts.append(content)
            elif role == "assistant":
                contents.append({"role": "model", "parts": [{"text": content}]})
            else:
                # 'user' or anything unknown — default to user
                contents.append({"role": "user", "parts": [{"text": content}]})
        system = "\n\n".join(p for p in system_parts if p) or None
        return system, contents

    async def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
        json_mode: bool = False,
        max_tokens: int = 2000,
    ) -> LLMResponse:
        system, contents = self._split_messages(messages)

        cfg_kwargs: dict = {
            "temperature": temperature,
            "max_output_tokens": max_tokens,
            # Gemini 2.5-flash burns "thinking" tokens against the same budget
            # as visible output. With thinking left at the default (~1024) a
            # ``max_output_tokens=700`` call gets truncated to zero visible
            # text. Disable thinking so the full budget lands in output.
            "thinking_config": self._types.ThinkingConfig(thinking_budget=0),
        }
        if system:
            cfg_kwargs["system_instruction"] = system
        if json_mode:
            cfg_kwargs["response_mime_type"] = "application/json"
        config = self._types.GenerateContentConfig(**cfg_kwargs)

        start = time.perf_counter()
        try:
            response = await self._client.aio.models.generate_content(
                model=self.model,
                contents=contents,
                config=config,
            )
        except Exception as e:  # noqa: BLE001
            self._raise_typed(e)
        latency_ms = int((time.perf_counter() - start) * 1000)

        text = (getattr(response, "text", None) or "").strip()
        usage = getattr(response, "usage_metadata", None)
        return LLMResponse(
            content=text,
            provider=self.name,
            model=self.model,
            input_tokens=int(getattr(usage, "prompt_token_count", 0) or 0),
            output_tokens=int(getattr(usage, "candidates_token_count", 0) or 0),
            latency_ms=latency_ms,
        )
