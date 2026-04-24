"""LLM fallback chain.

Tries providers in the order they were supplied. On ``QuotaExceededError`` the
provider is marked exhausted *until* a retry-at timestamp (default 90s from
now) — this covers both true daily exhaustion and per-minute rate limits
without killing the whole run when a provider merely needs to cool off. On
``ProviderError`` we fall back without marking. Every provider call,
successful or not, is recorded to ``llm_calls`` via
``src.utils.llm_telemetry.record_call``.
"""
from __future__ import annotations

import asyncio
import time

from src.utils.llm_providers import (
    LLMProvider,
    LLMResponse,
    ProviderError,
    QuotaExceededError,
)
from src.utils.llm_telemetry import record_call
from src.utils.logger import get_logger

log = get_logger(__name__)

# How long a provider stays on the bench after a 429. Free-tier RPM/TPM
# budgets typically refresh inside 60s; 90s gives a safety margin without
# stalling the pipeline for so long that daily-exhaustion errors masquerade
# as transient.
RATE_LIMIT_COOLDOWN_S = 90.0

# When every provider is cooling, wait up to this long for the earliest
# cooldown to expire before giving up. Keeps bulk scoring from burning
# through a batch with "all exhausted" errors while providers recover.
MAX_WAIT_FOR_COOLDOWN_S = 100.0


class LLMFallbackChain:
    def __init__(self, providers: list[LLMProvider]) -> None:
        if not providers:
            raise ValueError("LLMFallbackChain requires at least one provider")
        self.providers: list[LLMProvider] = list(providers)
        # key -> monotonic timestamp at which the provider is eligible again.
        self.exhausted: dict[str, float] = {}

    def _key(self, p: LLMProvider) -> str:
        return f"{p.name}:{p.model}"

    def _is_cooling(self, key: str, now: float) -> bool:
        retry_at = self.exhausted.get(key)
        if retry_at is None:
            return False
        if retry_at <= now:
            # Cooldown expired — clear the mark so telemetry reflects reality.
            del self.exhausted[key]
            return False
        return True

    async def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
        json_mode: bool = False,
        max_tokens: int = 2000,
    ) -> LLMResponse:
        last_error: Exception | None = None
        attempted_after_wait = False
        while True:
            for p in self.providers:
                key = self._key(p)
                if self._is_cooling(key, time.monotonic()):
                    continue
                try:
                    resp = await p.chat(
                        messages,
                        temperature=temperature,
                        json_mode=json_mode,
                        max_tokens=max_tokens,
                    )
                except QuotaExceededError as e:
                    self.exhausted[key] = time.monotonic() + RATE_LIMIT_COOLDOWN_S
                    log.warning(
                        "llm_chain.provider_quota_cooldown",
                        provider=key,
                        cooldown_s=RATE_LIMIT_COOLDOWN_S,
                        error=str(e)[:500],
                    )
                    _safe_record(p, kind="quota", err=str(e))
                    last_error = e
                    continue
                except ProviderError as e:
                    log.error(
                        "llm_chain.provider_error",
                        provider=key,
                        error=str(e)[:500],
                    )
                    _safe_record(p, kind="provider_error", err=str(e))
                    last_error = e
                    continue
                except Exception as e:  # noqa: BLE001 — unexpected types also fall back
                    log.error(
                        "llm_chain.provider_unknown_error",
                        provider=key,
                        error=str(e)[:500],
                    )
                    _safe_record(p, kind="unknown", err=str(e))
                    last_error = e
                    continue

                _safe_record_success(resp)
                return resp

            # Every provider was either cooling or just failed. If there's a
            # cooldown expiring soon, wait for it once and retry the loop.
            if attempted_after_wait or not self.exhausted:
                break
            now = time.monotonic()
            earliest = min(self.exhausted.values())
            wait_s = earliest - now
            if wait_s <= 0 or wait_s > MAX_WAIT_FOR_COOLDOWN_S:
                break
            log.info(
                "llm_chain.waiting_for_cooldown",
                wait_s=round(wait_s, 1),
            )
            await asyncio.sleep(wait_s + 0.5)
            attempted_after_wait = True

        raise QuotaExceededError(
            f"All providers exhausted. Last error: {last_error}"
        )


def _safe_record(p: LLMProvider, *, kind: str, err: str) -> None:
    try:
        record_call(
            provider=p.name,
            model=p.model,
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
            success=False,
            error=f"{kind}: {err[:400]}",
        )
    except Exception as e:  # noqa: BLE001
        log.warning("llm_chain.telemetry_failed", error=str(e))


def _safe_record_success(resp: LLMResponse) -> None:
    try:
        record_call(
            provider=resp.provider,
            model=resp.model,
            input_tokens=resp.input_tokens,
            output_tokens=resp.output_tokens,
            latency_ms=resp.latency_ms,
            success=True,
            error=None,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("llm_chain.telemetry_failed", error=str(e))
