"""LLM fallback-chain tests. Mocks only — never hits a real provider."""
from __future__ import annotations

from typing import Any

import pytest

from src.utils.llm_chain import LLMFallbackChain
from src.utils.llm_providers import (
    LLMProvider,
    LLMResponse,
    ProviderError,
    QuotaExceededError,
)


class _FakeProvider(LLMProvider):
    """Test double: replays a pre-programmed sequence of responses / errors."""

    def __init__(self, name: str, model: str, responses: list[Any]) -> None:
        self.name = name
        self.model = model
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
        json_mode: bool = False,
        max_tokens: int = 2000,
    ) -> LLMResponse:
        self.calls.append(
            {
                "messages": messages,
                "temperature": temperature,
                "json_mode": json_mode,
                "max_tokens": max_tokens,
            }
        )
        if not self._responses:
            raise AssertionError(
                f"{self.name}:{self.model} got a call with no remaining responses"
            )
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _ok(provider: str, model: str, content: str = "{}") -> LLMResponse:
    return LLMResponse(
        content=content,
        provider=provider,
        model=model,
        input_tokens=10,
        output_tokens=20,
        latency_ms=123,
    )


@pytest.fixture(autouse=True)
def _stub_telemetry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Chain writes a telemetry row after every call. Disable that path in
    tests so we don't need a live Supabase. Also collapse the cooldown-wait
    window so the ``all-exhausted`` path doesn't sleep for the full 90s."""
    import src.utils.llm_chain as chain_mod

    monkeypatch.setattr(chain_mod, "record_call", lambda **_: None)
    monkeypatch.setattr(chain_mod, "MAX_WAIT_FOR_COOLDOWN_S", 0.0)


@pytest.mark.asyncio
async def test_chain_uses_first_provider_when_available():
    primary = _FakeProvider("gemini", "flash", [_ok("gemini", "flash", "FIRST")])
    backup = _FakeProvider("groq", "70b", [_ok("groq", "70b", "BACKUP")])
    chain = LLMFallbackChain([primary, backup])

    resp = await chain.chat([{"role": "user", "content": "hi"}])
    assert resp.content == "FIRST"
    assert resp.provider == "gemini"
    # backup was never consulted
    assert backup.calls == []


@pytest.mark.asyncio
async def test_chain_falls_back_on_quota_error():
    primary = _FakeProvider(
        "gemini", "flash", [QuotaExceededError("daily cap")]
    )
    backup = _FakeProvider("groq", "70b", [_ok("groq", "70b", "FALLBACK")])
    chain = LLMFallbackChain([primary, backup])

    resp = await chain.chat([{"role": "user", "content": "hi"}])
    assert resp.content == "FALLBACK"
    assert resp.provider == "groq"
    # gemini marked exhausted
    assert "gemini:flash" in chain.exhausted


@pytest.mark.asyncio
async def test_chain_remembers_exhausted_provider():
    primary = _FakeProvider(
        "gemini", "flash", [QuotaExceededError("daily cap")]
    )
    backup = _FakeProvider(
        "groq",
        "70b",
        [_ok("groq", "70b", "A"), _ok("groq", "70b", "B")],
    )
    chain = LLMFallbackChain([primary, backup])

    # First call exhausts gemini, succeeds via groq.
    r1 = await chain.chat([{"role": "user", "content": "1"}])
    assert r1.content == "A"
    # Second call: gemini must NOT be re-tried. Primary had only one programmed
    # response, so a second call would blow up — if we hit it, test fails.
    r2 = await chain.chat([{"role": "user", "content": "2"}])
    assert r2.content == "B"
    assert len(primary.calls) == 1  # never re-consulted
    assert len(backup.calls) == 2


@pytest.mark.asyncio
async def test_chain_raises_when_all_exhausted():
    primary = _FakeProvider("gemini", "flash", [QuotaExceededError("x")])
    backup = _FakeProvider("groq", "70b", [QuotaExceededError("y")])
    chain = LLMFallbackChain([primary, backup])

    with pytest.raises(QuotaExceededError) as excinfo:
        await chain.chat([{"role": "user", "content": "hi"}])
    assert "All providers exhausted" in str(excinfo.value)


@pytest.mark.asyncio
async def test_chain_skips_provider_on_error_but_tries_next():
    # Non-quota ProviderError should also trigger fallback (transient 5xx,
    # malformed response, etc.). Crucially, the provider is NOT marked
    # exhausted — a later call in the same chain could retry it.
    primary = _FakeProvider(
        "gemini",
        "flash",
        [ProviderError("500 internal"), _ok("gemini", "flash", "RECOVERED")],
    )
    backup = _FakeProvider("groq", "70b", [_ok("groq", "70b", "BACKUP")])
    chain = LLMFallbackChain([primary, backup])

    # First call: primary errors → backup answers.
    r1 = await chain.chat([{"role": "user", "content": "1"}])
    assert r1.provider == "groq"
    assert "gemini:flash" not in chain.exhausted  # not marked exhausted

    # Second call: primary is still in rotation, returns success.
    r2 = await chain.chat([{"role": "user", "content": "2"}])
    assert r2.content == "RECOVERED"
    assert r2.provider == "gemini"


@pytest.mark.asyncio
async def test_chain_constructor_rejects_empty_list():
    with pytest.raises(ValueError):
        LLMFallbackChain([])


@pytest.mark.asyncio
async def test_chain_forwards_kwargs():
    primary = _FakeProvider("gemini", "flash", [_ok("gemini", "flash", "{}")])
    chain = LLMFallbackChain([primary])

    await chain.chat(
        [{"role": "user", "content": "hi"}],
        temperature=0.7,
        json_mode=True,
        max_tokens=500,
    )
    call = primary.calls[0]
    assert call["temperature"] == 0.7
    assert call["json_mode"] is True
    assert call["max_tokens"] == 500
