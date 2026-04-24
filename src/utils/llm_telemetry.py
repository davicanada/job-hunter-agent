"""Telemetry context + recorder for LLM chain calls.

The scoring / tailor / cover-letter entry points push a ``run_id`` and
``stage`` via ``llm_context(...)`` before invoking the chain. The chain reads
those contextvars after each provider call and writes one row to
``llm_calls``. Failures are swallowed — telemetry never breaks the pipeline.
"""
from __future__ import annotations

import contextvars
from typing import Any

from src.utils.logger import get_logger

log = get_logger(__name__)

_run_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "llm_run_id", default=None
)
_stage_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "llm_stage", default=None
)


class llm_context:
    """Context manager to tag the active ``run_id`` and ``stage``.

    Usage::

        with llm_context(run_id=run_id, stage="score"):
            scored = await score_jobs(...)

    Nested contexts compose: inner values override, and ``__exit__`` restores
    whatever was set by the outer scope. Works in asyncio because
    ``contextvars`` are copied per-task by the event loop.
    """

    def __init__(self, run_id: str | None = None, stage: str | None = None) -> None:
        self.run_id = run_id
        self.stage = stage
        self._tokens: list[tuple[contextvars.ContextVar, contextvars.Token]] = []

    def __enter__(self) -> "llm_context":
        if self.run_id is not None:
            self._tokens.append((_run_id_var, _run_id_var.set(self.run_id)))
        if self.stage is not None:
            self._tokens.append((_stage_var, _stage_var.set(self.stage)))
        return self

    def __exit__(self, *exc: Any) -> None:
        for var, tok in reversed(self._tokens):
            var.reset(tok)


def current_run_id() -> str | None:
    return _run_id_var.get()


def current_stage() -> str | None:
    return _stage_var.get()


def record_call(
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    latency_ms: int,
    success: bool,
    error: str | None = None,
) -> None:
    """Insert one ``llm_calls`` row. Runs inline (sync DB call is ~50ms);
    all failures are logged and swallowed so a telemetry outage never affects
    the scoring path."""
    run_id = current_run_id()
    stage = current_stage() or "unknown"
    try:
        # Local import avoids circular: db/client → models → … → llm_telemetry
        from src.db.client import record_llm_call

        record_llm_call(
            run_id=run_id,
            stage=stage,
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            success=success,
            error=error,
        )
    except Exception as e:  # noqa: BLE001
        log.warning(
            "llm_telemetry.record_failed",
            provider=provider,
            model=model,
            error=str(e),
        )
