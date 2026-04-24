"""Per-job writing orchestrator + batch runner.

Two entry points:

* ``write_materials_for_job`` — tailor + cover letter + .docx for one scored
  job. All failures are caught and returned as ``None``; nothing ever
  escapes to kill the batch.
* ``write_all_matching`` — fan out over a list of ``ScoredJob`` records,
  capped at ``Semaphore(3)`` because each call is two LLM prompts with a
  fat payload.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from src.db.client import upsert_application
from src.models.job import Job, ScoredJob
from src.utils.llm_telemetry import llm_context
from src.utils.logger import get_logger
from src.writing.cover_letter import detect_language, generate_cover_letter
from src.writing.docx_builder import (
    build_cover_letter_docx,
    build_tailored_resume_docx,
)
from src.writing.paths import make_job_output_paths, make_run_output_dir
from src.writing.resume_tailor import tailor_resume_content

log = get_logger(__name__)

WRITING_CONCURRENCY = 3


class WriteResult(BaseModel):
    total_eligible: int = 0
    written: int = 0
    failed: int = 0
    errors: dict[str, str] = Field(default_factory=dict)
    outputs: list[dict[str, Any]] = Field(default_factory=list)


def _is_eligible(scored: ScoredJob, min_score: int) -> bool:
    if scored.score < min_score:
        return False
    if scored.verdict == "skip":
        return False
    if scored.auth_status == "blocked_citizen_only":
        return False
    return True


async def write_materials_for_job(
    scored_job: ScoredJob,
    profile: dict,
    output_dir: Path,
) -> tuple[Path, Path] | None:
    """Produce a tailored resume + cover letter .docx for one scored job.

    Returns ``(resume_path, cover_letter_path)`` on success, ``None`` on
    any failure. Failures are logged, never raised.
    """
    job: Job | None = scored_job.job
    if job is None:
        log.error(
            "writer.missing_job",
            scored_job_id=str(scored_job.id),
        )
        return None

    try:
        with llm_context(stage="tailor"):
            tailored = await tailor_resume_content(
                profile, job, scored_job.track or "other"
            )
    except Exception as e:  # noqa: BLE001
        log.error(
            "writer.tailor_failed", job_id=str(job.id), error=str(e)
        )
        return None

    try:
        with llm_context(stage="cover_letter"):
            cover_text = await generate_cover_letter(profile, job, scored_job)
    except Exception as e:  # noqa: BLE001
        log.error(
            "writer.cover_letter_failed",
            job_id=str(job.id),
            error=str(e),
        )
        cover_text = ""

    resume_path, cover_path = make_job_output_paths(output_dir, job)

    try:
        build_tailored_resume_docx(profile, tailored, resume_path)
    except Exception as e:  # noqa: BLE001
        log.error(
            "writer.resume_docx_failed",
            job_id=str(job.id),
            path=str(resume_path),
            error=str(e),
        )
        return None

    try:
        language = detect_language(job.description)
        build_cover_letter_docx(
            profile, job, cover_text, cover_path, language=language
        )
    except Exception as e:  # noqa: BLE001
        log.error(
            "writer.cover_docx_failed",
            job_id=str(job.id),
            path=str(cover_path),
            error=str(e),
        )
        return None

    log.info(
        "writer.materials_ready",
        job_id=str(job.id),
        company=job.company,
        title=job.title,
        resume=str(resume_path),
        cover=str(cover_path),
    )
    return resume_path, cover_path


async def write_all_matching(
    scored_jobs: list[ScoredJob],
    run_id: str,
    min_score: int,
    profile: dict,
) -> WriteResult:
    """Generate .docx materials for every eligible scored job.

    Eligibility: score >= ``min_score`` AND verdict != ``skip`` AND
    auth_status != ``blocked_citizen_only``. Writes a concurrent batch
    capped by ``WRITING_CONCURRENCY``, upserts an ``applications`` row per
    success.
    """
    eligible = [s for s in scored_jobs if _is_eligible(s, min_score)]
    result = WriteResult(total_eligible=len(eligible))
    if not eligible:
        return result

    run_dir = make_run_output_dir(run_id)
    sem = asyncio.Semaphore(WRITING_CONCURRENCY)

    async def _one(scored: ScoredJob) -> tuple[ScoredJob, tuple[Path, Path] | None]:
        async with sem:
            paths = await write_materials_for_job(scored, profile, run_dir)
        return scored, paths

    pairs = await asyncio.gather(*(_one(s) for s in eligible))

    tracks: Counter[str] = Counter()
    for scored, paths in pairs:
        job = scored.job
        key = str(scored.id)
        if paths is None:
            result.failed += 1
            result.errors[key] = "write failed"
            continue
        resume_path, cover_path = paths
        try:
            upsert_application(
                scored_job_id=str(scored.id),
                resume_path=str(resume_path),
                cover_letter_path=str(cover_path),
                status="suggested",
            )
        except Exception as e:  # noqa: BLE001
            result.failed += 1
            result.errors[key] = f"applications upsert failed: {e}"
            continue
        result.written += 1
        tracks[scored.track or "other"] += 1
        result.outputs.append(
            {
                "scored_job_id": key,
                "job_id": str(job.id) if job else None,
                "company": job.company if job else None,
                "title": job.title if job else None,
                "score": scored.score,
                "verdict": scored.verdict,
                "track": scored.track,
                "resume_path": str(resume_path),
                "cover_letter_path": str(cover_path),
            }
        )

    log.info(
        "writer.batch_finished",
        eligible=result.total_eligible,
        written=result.written,
        failed=result.failed,
        by_track=dict(tracks),
    )
    return result
