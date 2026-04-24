"""Orchestrator: run all enabled source adapters in parallel and dedupe within the batch."""
from __future__ import annotations

import asyncio
import time

from config.sources import SOURCES
from src.models.job import FetchResult, Job
from src.sources.base import JobSourceAdapter
from src.sources.himalayas import HimalayasAdapter
from src.sources.indeed_rss import IndeedRSSAdapter
from src.sources.jobicy import JobicyAdapter
from src.sources.linkedin_rss import LinkedInRSSAdapter
from src.sources.remoteok import RemoteOKAdapter
from src.sources.remotive import RemotiveAdapter
from src.sources.working_nomads import WorkingNomadsAdapter
from src.sources.wwr import WWRAdapter
from src.utils.logger import get_logger

log = get_logger(__name__)

_ADAPTER_REGISTRY: dict[str, type[JobSourceAdapter]] = {
    "remoteok": RemoteOKAdapter,
    "wwr": WWRAdapter,
    "remotive": RemotiveAdapter,
    "working_nomads": WorkingNomadsAdapter,
    "jobicy": JobicyAdapter,
    "himalayas": HimalayasAdapter,
    "linkedin_rss": LinkedInRSSAdapter,
    "indeed_rss": IndeedRSSAdapter,
}


def _enabled_adapters() -> list[JobSourceAdapter]:
    adapters: list[JobSourceAdapter] = []
    for cfg in SOURCES:
        if not cfg.get("enabled", False):
            continue
        name = cfg.get("name")
        klass = _ADAPTER_REGISTRY.get(name) if name else None
        if klass is None:
            log.warning("fetcher.unknown_source", name=name)
            continue
        adapters.append(klass())
    return adapters


async def _run_adapter(adapter: JobSourceAdapter) -> tuple[str, list[Job] | Exception, float]:
    start = time.perf_counter()
    try:
        jobs = await adapter.fetch()
        elapsed = time.perf_counter() - start
        return adapter.source_name, jobs, elapsed
    except Exception as e:  # noqa: BLE001 — soft failure
        elapsed = time.perf_counter() - start
        return adapter.source_name, e, elapsed


async def fetch_all_sources() -> FetchResult:
    """Fetch from all enabled sources in parallel.

    - Each adapter is treated as a soft failure (one source down → others still run).
    - After aggregation, dedupe by ``external_id`` (jobs can appear on multiple boards).
    - Does NOT insert to DB; caller handles persistence.
    """
    adapters = _enabled_adapters()
    log.info("fetcher.start", sources=[a.source_name for a in adapters])

    results = await asyncio.gather(
        *(_run_adapter(a) for a in adapters),
        return_exceptions=False,  # _run_adapter already catches
    )

    per_source: dict[str, int] = {}
    errors: dict[str, str] = {}
    merged: list[Job] = []

    for name, outcome, elapsed in results:
        if isinstance(outcome, Exception):
            errors[name] = f"{type(outcome).__name__}: {outcome}"
            per_source[name] = 0
            log.error(
                "fetcher.source_failed",
                source=name,
                elapsed_s=round(elapsed, 2),
                error=errors[name],
            )
            continue
        per_source[name] = len(outcome)
        merged.extend(outcome)
        log.info(
            "fetcher.source_ok",
            source=name,
            count=len(outcome),
            elapsed_s=round(elapsed, 2),
        )

    total_fetched = len(merged)

    # In-batch dedupe — keep first occurrence of each external_id.
    seen: set[str] = set()
    unique: list[Job] = []
    duplicates = 0
    for job in merged:
        if job.external_id in seen:
            duplicates += 1
            continue
        seen.add(job.external_id)
        unique.append(job)

    log.info(
        "fetcher.done",
        total_fetched=total_fetched,
        total_unique=len(unique),
        in_batch_duplicates=duplicates,
        errors=list(errors.keys()),
    )

    return FetchResult(
        total_fetched=total_fetched,
        total_unique=len(unique),
        per_source=per_source,
        errors=errors,
        jobs=unique,
    )
