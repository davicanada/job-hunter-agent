"""LLM scoring orchestrator.

Loads prefilter-surviving jobs, fans out to Groq with a ``Semaphore(5)`` cap,
parses the strict-JSON response into a ``ScoredJob``, and persists. Jobs that
fail the prefilter, earn a ``skip`` verdict, or hit the ``blocked_citizen_only``
auth_status are recorded in ``skipped_jobs`` with a ``skip_stage`` tag so later
analysis can explain the funnel.
"""
from __future__ import annotations

import asyncio
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from config.settings import settings
from src.db.client import (
    delete_skipped_jobs,
    insert_scored_job,
    insert_skipped_job,
    load_jobs_by_ids,
    load_prefilter_skipped_jobs,
)
from src.models.job import Job, ScoredJob
from src.scoring.prefilter import PrefilterResult, prefilter_job
from src.utils.llm import chat_with_meta
from src.utils.llm_telemetry import llm_context
from src.utils.logger import get_logger

log = get_logger(__name__)

_SCORER_PROMPT_PATH = Path("data/prompts/scorer.txt")
_PROFILE_PATH = Path("data/profile.json")

SCORING_CONCURRENCY = 1
PER_JOB_TIMEOUT_S = 180
SCORE_PROMPT_DESCRIPTION_LIMIT = 2000
# Minimum gap between consecutive chain calls in bulk runs. Gemini's free tier
# caps at 10 RPM; 6.5s pacing stays under that while still letting a 50-job
# batch finish in ~6 minutes.
BULK_SCORE_MIN_INTERVAL_S = 6.5


class ScoringResult(BaseModel):
    total_jobs: int = 0
    prefiltered_out: int = 0
    llm_scored: int = 0
    by_verdict: dict[str, int] = Field(default_factory=dict)
    by_track: dict[str, int] = Field(default_factory=dict)
    errors: dict[str, str] = Field(default_factory=dict)
    scored_job_ids: list[str] = Field(default_factory=list)


class ReconcileResult(BaseModel):
    examined: int = 0
    requeued: int = 0
    still_rejected: int = 0
    requeued_job_ids: list[str] = Field(default_factory=list)


def _load_profile() -> dict:
    return json.loads(_PROFILE_PATH.read_text(encoding="utf-8"))


def _load_scorer_prompt() -> str:
    return _SCORER_PROMPT_PATH.read_text(encoding="utf-8")


def _job_payload_for_prompt(job: Job) -> dict[str, Any]:
    """Compact JSON-safe job view passed to the LLM prompt."""
    description = (job.description or "")[:SCORE_PROMPT_DESCRIPTION_LIMIT]
    return {
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "is_remote": job.is_remote,
        "allows_target_region": job.allows_target_region,
        "employment_type": job.employment_type,
        "salary_min": job.salary_min,
        "salary_max": job.salary_max,
        "salary_currency": job.salary_currency,
        "url": job.url,
        "source": job.source,
        "description": description,
    }


def compute_recency_delta(
    posted_at: datetime | None,
    now: datetime | None = None,
) -> tuple[int, int | None]:
    """Tiered recency weighting — returns ``(delta, age_days)``.

    Tiers (age = ``now`` - ``posted_at``, rounded down to full days):

    * ≤ 2 days   → +3   (hot)
    * 3–7 days   →  0   (normal)
    * 8–14 days  → -5
    * 15–30 days → -10
    * 31–45 days → -20
    * 46+ days   → -20  (same floor — treat old as old)
    * ``None``   →  0, age_days=None  (unknown posting date, don't penalise)
    * future     →  0, age_days=0 with warning (clock skew / bad source)

    ``now`` is injected for tests; defaults to ``datetime.now(timezone.utc)``.
    """
    if posted_at is None:
        return 0, None
    if now is None:
        now = datetime.now(timezone.utc)

    if posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=timezone.utc)

    delta_seconds = (now - posted_at).total_seconds()
    if delta_seconds < 0:
        log.warning(
            "scorer.recency.future_posted_at",
            posted_at=posted_at.isoformat(),
            now=now.isoformat(),
        )
        return 0, 0

    age_days = int(delta_seconds // 86400)
    if age_days <= 2:
        return 3, age_days
    if age_days <= 7:
        return 0, age_days
    if age_days <= 14:
        return -5, age_days
    if age_days <= 30:
        return -10, age_days
    return -20, age_days


def _render_scorer_prompt(
    template: str,
    profile: dict,
    job: Job,
    prefilter: PrefilterResult,
) -> str:
    profile_json = json.dumps(profile, ensure_ascii=False)
    job_json = json.dumps(_job_payload_for_prompt(job), ensure_ascii=False)
    hint_json = json.dumps(
        {
            "seniority_hint": prefilter.seniority_hint,
            "prefilter_notes": prefilter.notes,
        },
        ensure_ascii=False,
    )
    return (
        template.replace("{profile_json}", profile_json)
        .replace("{job_json}", job_json)
        .replace("{prefilter_json}", hint_json)
    )


async def score_single_job(
    job: Job,
    profile: dict,
    prompt_template: str,
    prefilter: PrefilterResult,
) -> ScoredJob | None:
    """Call the chain, parse JSON, return a ``ScoredJob``. Returns ``None`` on
    any failure — parsing, validation, network. All failures are logged. The
    model column is stamped with the actual provider:model that answered.
    """
    prompt = _render_scorer_prompt(prompt_template, profile, job, prefilter)
    messages = [{"role": "user", "content": prompt}]
    try:
        resp = await asyncio.wait_for(
            chat_with_meta(
                messages,
                temperature=0.2,
                json_mode=True,
                max_tokens=900,
            ),
            timeout=PER_JOB_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        log.error("scorer.llm.timeout", job_id=str(job.id), title=job.title)
        return None
    except Exception as e:  # noqa: BLE001
        log.error(
            "scorer.llm.call_failed",
            job_id=str(job.id),
            title=job.title,
            error=str(e),
        )
        return None

    try:
        raw = json.loads(resp.content)
    except json.JSONDecodeError as e:
        log.error(
            "scorer.llm.json_parse_failed",
            job_id=str(job.id),
            error=str(e),
            snippet=resp.content[:500],
        )
        return None

    if not isinstance(raw, dict):
        log.error(
            "scorer.llm.unexpected_type",
            job_id=str(job.id),
            got_type=type(raw).__name__,
        )
        return None

    try:
        llm_score = int(raw.get("score", 0))
        delta, age_days = compute_recency_delta(job.posted_at)
        final_score = max(0, min(100, llm_score + delta))
        why_match = raw.get("why_match")
        if delta != 0 and age_days is not None:
            if delta > 0:
                note = f"Recency bonus +{delta} (posted {age_days}d ago)."
            else:
                note = f"Recency penalty {delta} (posted {age_days}d ago)."
            why_match = f"{why_match} {note}".strip() if why_match else note

        return ScoredJob(
            job_id=job.id,  # type: ignore[arg-type]
            score=final_score,
            verdict=raw.get("verdict", "skip"),
            track=raw.get("track"),
            why_match=why_match,
            watch_out=raw.get("watch_out"),
            auth_status=raw.get("auth_status"),
            model=f"{resp.provider}:{resp.model}",
            recency_bonus=delta,
            age_days=age_days,
        )
    except (ValidationError, ValueError) as e:
        snippet = json.dumps(raw)[:1000]
        log.error(
            "scorer.validation.failed",
            job_id=str(job.id),
            error=str(e),
            raw_snippet=snippet,
        )
        return None


def reconcile_skipped_jobs(limit: int = 500) -> ReconcileResult:
    """Re-evaluate ``prefilter``-skipped jobs against the *current* prefilter.

    Rows written under older rules can become stale after source or seniority
    policy changes. This helper reruns the current prefilter and, for anything
    now accepted, deletes the ``skipped_jobs`` entry so the caller can feed the
    job back into ``score_jobs``.

    Returns a ``ReconcileResult`` with the list of re-queued ``job_id``s.
    Never re-scores directly — the caller decides when to trigger scoring.
    """
    jobs = load_prefilter_skipped_jobs(limit=limit)
    if not jobs:
        return ReconcileResult()

    profile = _load_profile()
    requeued_ids: list[str] = []
    still_rejected = 0
    for job in jobs:
        pf = prefilter_job(job, profile)
        if pf.should_score:
            requeued_ids.append(str(job.id))
        else:
            still_rejected += 1

    if requeued_ids:
        try:
            delete_skipped_jobs(requeued_ids)
        except Exception as e:  # noqa: BLE001
            log.error("scorer.reconcile.delete_failed", error=str(e))
            # The delete failed — don't surface the IDs as re-queueable,
            # otherwise score_jobs would run and insert_skipped_job would
            # then fight the UNIQUE(job_id) constraint on a retry.
            return ReconcileResult(
                examined=len(jobs),
                requeued=0,
                still_rejected=still_rejected,
                requeued_job_ids=[],
            )

    log.info(
        "scorer.reconcile.done",
        examined=len(jobs),
        requeued=len(requeued_ids),
        still_rejected=still_rejected,
    )
    return ReconcileResult(
        examined=len(jobs),
        requeued=len(requeued_ids),
        still_rejected=still_rejected,
        requeued_job_ids=requeued_ids,
    )


async def score_jobs(job_ids: list[str]) -> ScoringResult:
    """Full scoring pass: prefilter → LLM → persist.

    Jobs that fail the prefilter or come back with a ``skip`` verdict /
    ``blocked_citizen_only`` auth get a row in ``skipped_jobs``. Everything
    else lands in ``scored_jobs``.
    """
    if not job_ids:
        return ScoringResult()

    jobs = load_jobs_by_ids(job_ids)
    if not jobs:
        log.warning("scorer.no_jobs_loaded", requested=len(job_ids))
        return ScoringResult(total_jobs=0)

    profile = _load_profile()
    prompt_template = _load_scorer_prompt()

    by_verdict: Counter[str] = Counter()
    by_track: Counter[str] = Counter()
    errors: dict[str, str] = {}
    scored_ids: list[str] = []

    survivors: list[tuple[Job, PrefilterResult]] = []
    prefiltered_out = 0
    for job in jobs:
        pf = prefilter_job(job, profile)
        if not pf.should_score:
            prefiltered_out += 1
            try:
                insert_skipped_job(
                    str(job.id), pf.skip_reason or "prefilter", "prefilter"
                )
            except Exception as e:  # noqa: BLE001
                errors[str(job.id)] = f"skipped_jobs insert failed: {e}"
            log.info(
                "scorer.prefiltered",
                job_id=str(job.id),
                title=job.title,
                reason=pf.skip_reason,
            )
            continue
        survivors.append((job, pf))

    sem = asyncio.Semaphore(SCORING_CONCURRENCY)

    async def _run(job: Job, pf: PrefilterResult) -> tuple[Job, ScoredJob | None]:
        async with sem:
            scored = await score_single_job(job, profile, prompt_template, pf)
            # Pace bulk runs so we don't burst past Gemini's 10 RPM free-tier
            # ceiling. Serial semaphore + this sleep give ~8.5 RPM effective.
            await asyncio.sleep(BULK_SCORE_MIN_INTERVAL_S)
        return job, scored

    with llm_context(stage="score"):
        pairs = await asyncio.gather(*(_run(j, pf) for j, pf in survivors))

    llm_scored = 0
    for job, scored in pairs:
        job_id_str = str(job.id)
        if scored is None:
            errors[job_id_str] = "llm scoring failed"
            by_verdict["error"] += 1
            continue
        try:
            new_id = insert_scored_job(scored)
            scored_ids.append(new_id)
            llm_scored += 1
            by_verdict[scored.verdict] += 1
            by_track[scored.track or "other"] += 1
        except Exception as e:  # noqa: BLE001
            errors[job_id_str] = f"scored_jobs insert failed: {e}"
            by_verdict["error"] += 1
            log.error(
                "scorer.persist.failed",
                job_id=job_id_str,
                error=str(e),
            )
            continue

        if scored.verdict == "skip":
            try:
                insert_skipped_job(
                    job_id_str, "LLM verdict: skip", "llm_verdict_skip"
                )
            except Exception as e:  # noqa: BLE001
                errors[job_id_str] = f"skipped_jobs verdict_skip failed: {e}"
        elif scored.auth_status == "blocked_citizen_only":
            try:
                insert_skipped_job(
                    job_id_str,
                    "Citizenship / clearance requirement",
                    "auth_blocked",
                )
            except Exception as e:  # noqa: BLE001
                errors[job_id_str] = f"skipped_jobs auth_blocked failed: {e}"

    result = ScoringResult(
        total_jobs=len(jobs),
        prefiltered_out=prefiltered_out,
        llm_scored=llm_scored,
        by_verdict=dict(by_verdict),
        by_track=dict(by_track),
        errors=errors,
        scored_job_ids=scored_ids,
    )
    log.info(
        "scorer.run_finished",
        total=result.total_jobs,
        prefiltered_out=result.prefiltered_out,
        llm_scored=result.llm_scored,
        by_verdict=result.by_verdict,
        errors=len(result.errors),
    )
    return result
