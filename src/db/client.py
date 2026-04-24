"""Supabase client singleton and DB helpers.

All write helpers return the new row's ``id`` as a string. They log and re-raise
on failure so callers can decide whether to abort the run or continue.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from supabase import Client, create_client

from config.settings import settings
from src.models.job import Application, Job, ScoredJob
from src.utils.logger import get_logger

log = get_logger(__name__)

_client: Client | None = None


def get_client() -> Client:
    global _client
    if _client is None:
        _client = create_client(settings.supabase_url, settings.supabase_service_key)
    return _client


def _one(result: Any, op: str) -> dict[str, Any]:
    data = getattr(result, "data", None) or []
    if not data:
        raise RuntimeError(f"Supabase {op} returned no rows")
    return data[0]


def insert_job(job: Job) -> str:
    """Upsert a job by (source, external_id). Returns the job UUID."""
    client = get_client()
    payload = job.model_dump(mode="json", exclude={"id", "discovered_at"})
    try:
        result = (
            client.table("jobs")
            .upsert(payload, on_conflict="source,external_id")
            .execute()
        )
        return _one(result, "upsert jobs")["id"]
    except Exception as e:
        log.error(
            "db.insert_job.failed",
            source=job.source,
            external_id=job.external_id,
            error=str(e),
        )
        raise


def job_exists(source: str, external_id: str) -> bool:
    client = get_client()
    try:
        result = (
            client.table("jobs")
            .select("id")
            .eq("source", source)
            .eq("external_id", external_id)
            .limit(1)
            .execute()
        )
        return bool(getattr(result, "data", None))
    except Exception as e:
        log.error(
            "db.job_exists.failed",
            source=source,
            external_id=external_id,
            error=str(e),
        )
        raise


def persist_new_jobs(jobs: list[Job]) -> tuple[list[str], int]:
    """Insert only jobs that don't already exist in DB (by (source, external_id)).

    Returns ``(new_ids, duplicates_skipped)``. Uses one SELECT to identify
    existing rows, then one batch INSERT of the remainder.
    """
    if not jobs:
        return [], 0

    client = get_client()
    seen_keys = {(j.source, j.external_id) for j in jobs}
    by_source: dict[str, list[str]] = {}
    for source, external_id in seen_keys:
        by_source.setdefault(source, []).append(external_id)

    existing_keys: set[tuple[str, str]] = set()
    try:
        for source, ids in by_source.items():
            # Supabase `.in_` has a practical cap; chunk by 200 to be safe.
            for i in range(0, len(ids), 200):
                chunk = ids[i : i + 200]
                result = (
                    client.table("jobs")
                    .select("source,external_id")
                    .eq("source", source)
                    .in_("external_id", chunk)
                    .execute()
                )
                for row in getattr(result, "data", []) or []:
                    existing_keys.add((row["source"], row["external_id"]))
    except Exception as e:
        log.error("db.persist_new_jobs.existence_check_failed", error=str(e))
        raise

    new_jobs = [
        j for j in jobs if (j.source, j.external_id) not in existing_keys
    ]
    duplicates = len(jobs) - len(new_jobs)

    if not new_jobs:
        log.info("db.persist_new_jobs.all_duplicates", total=len(jobs))
        return [], duplicates

    payloads = [
        j.model_dump(mode="json", exclude={"id", "discovered_at"}) for j in new_jobs
    ]
    try:
        result = client.table("jobs").insert(payloads).execute()
    except Exception as e:
        log.error(
            "db.persist_new_jobs.insert_failed",
            count=len(payloads),
            error=str(e),
        )
        raise

    new_ids = [row["id"] for row in getattr(result, "data", []) or []]
    log.info(
        "db.persist_new_jobs.ok",
        inserted=len(new_ids),
        duplicates=duplicates,
    )
    return new_ids, duplicates


def insert_scored_job(scored: ScoredJob) -> str:
    client = get_client()
    payload = scored.model_dump(
        mode="json", exclude={"id", "scored_at", "job"}
    )
    try:
        result = client.table("scored_jobs").insert(payload).execute()
        return _one(result, "insert scored_jobs")["id"]
    except Exception as e:
        log.error(
            "db.insert_scored_job.failed",
            job_id=str(scored.job_id),
            error=str(e),
        )
        raise


def insert_application(app: Application) -> str:
    client = get_client()
    payload = app.model_dump(mode="json", exclude={"id", "updated_at"})
    try:
        result = client.table("applications").insert(payload).execute()
        return _one(result, "insert applications")["id"]
    except Exception as e:
        log.error(
            "db.insert_application.failed",
            scored_job_id=str(app.scored_job_id),
            error=str(e),
        )
        raise


def upsert_application(
    scored_job_id: str,
    resume_path: str | None,
    cover_letter_path: str | None,
    status: str = "suggested",
) -> str:
    """Create or update the applications row for ``scored_job_id``.

    Upserts on the UNIQUE constraint so re-runs stay idempotent. Returns the
    application UUID.
    """
    client = get_client()
    payload: dict[str, Any] = {
        "scored_job_id": scored_job_id,
        "resume_path": resume_path,
        "cover_letter_path": cover_letter_path,
        "status": status,
    }
    try:
        result = (
            client.table("applications")
            .upsert(payload, on_conflict="scored_job_id")
            .execute()
        )
        return _one(result, "upsert applications")["id"]
    except Exception as e:
        log.error(
            "db.upsert_application.failed",
            scored_job_id=scored_job_id,
            error=str(e),
        )
        raise


def insert_skipped_job(job_id: str, reason: str, stage: str) -> None:
    """Record a job that was not scored / not pursued.

    ``stage`` is one of ``prefilter``, ``llm_verdict_skip``, ``auth_blocked``.
    Idempotent via the UNIQUE(job_id) constraint on ``skipped_jobs``.
    """
    client = get_client()
    payload = {"job_id": job_id, "skip_reason": reason, "skip_stage": stage}
    try:
        (
            client.table("skipped_jobs")
            .upsert(payload, on_conflict="job_id")
            .execute()
        )
    except Exception as e:
        log.error(
            "db.insert_skipped_job.failed",
            job_id=job_id,
            stage=stage,
            error=str(e),
        )
        raise


def load_prefilter_skipped_jobs(limit: int = 500) -> list[Job]:
    """Return jobs whose most recent skip row was a ``prefilter`` reject.

    Used by ``reconcile_skipped_jobs`` when the prefilter rules have been
    relaxed and some of those rows deserve a second look.
    """
    client = get_client()
    try:
        skip_rows = (
            client.table("skipped_jobs")
            .select("job_id")
            .eq("skip_stage", "prefilter")
            .limit(limit)
            .execute()
        )
    except Exception as e:
        log.error("db.load_prefilter_skipped_jobs.fetch_ids_failed", error=str(e))
        raise
    job_ids = [row["job_id"] for row in getattr(skip_rows, "data", []) or []]
    if not job_ids:
        return []
    return load_jobs_by_ids(job_ids)


def delete_skipped_jobs(job_ids: list[str]) -> int:
    """Delete rows from ``skipped_jobs`` by ``job_id``. Returns rows affected."""
    if not job_ids:
        return 0
    client = get_client()
    deleted = 0
    try:
        for i in range(0, len(job_ids), 200):
            chunk = job_ids[i : i + 200]
            result = (
                client.table("skipped_jobs")
                .delete()
                .in_("job_id", chunk)
                .execute()
            )
            deleted += len(getattr(result, "data", []) or [])
    except Exception as e:
        log.error(
            "db.delete_skipped_jobs.failed",
            count=len(job_ids),
            error=str(e),
        )
        raise
    return deleted


def load_jobs_by_ids(job_ids: list[str]) -> list[Job]:
    """Load ``jobs`` rows for the given UUIDs. Chunks of 200 to stay under
    Supabase's ``.in_`` limit. Returns hydrated ``Job`` models."""
    if not job_ids:
        return []
    client = get_client()
    out: list[Job] = []
    try:
        for i in range(0, len(job_ids), 200):
            chunk = job_ids[i : i + 200]
            result = (
                client.table("jobs")
                .select("*")
                .in_("id", chunk)
                .execute()
            )
            for row in getattr(result, "data", []) or []:
                out.append(Job.model_validate(row))
    except Exception as e:
        log.error("db.load_jobs_by_ids.failed", count=len(job_ids), error=str(e))
        raise
    return out


def load_unscored_recent_jobs(hours: int = 48) -> list[Job]:
    """Return recent jobs that do not yet have a scored_jobs or skipped_jobs row.

    Used by ``run_scoring_only`` for re-running scoring without re-fetching.
    """
    client = get_client()
    cutoff = datetime.now(timezone.utc).isoformat()
    # supabase-py doesn't expose interval math cleanly — fetch in Python.
    # We'll over-select the time window and exclude already-processed rows.
    try:
        scored_ids = {
            row["job_id"]
            for row in getattr(
                client.table("scored_jobs").select("job_id").execute(),
                "data",
                [],
            )
            or []
        }
        skipped_ids = {
            row["job_id"]
            for row in getattr(
                client.table("skipped_jobs").select("job_id").execute(),
                "data",
                [],
            )
            or []
        }
        recent = (
            client.table("jobs")
            .select("*")
            .gte(
                "discovered_at",
                (
                    datetime.now(timezone.utc).replace(microsecond=0)
                    - _hours_delta(hours)
                ).isoformat(),
            )
            .order("discovered_at", desc=True)
            .execute()
        )
    except Exception as e:
        log.error("db.load_unscored_recent_jobs.failed", error=str(e), cutoff=cutoff)
        raise

    processed = scored_ids | skipped_ids
    out: list[Job] = []
    for row in getattr(recent, "data", []) or []:
        if row["id"] in processed:
            continue
        out.append(Job.model_validate(row))
    return out


def _hours_delta(hours: int):
    from datetime import timedelta

    return timedelta(hours=hours)


def load_all_unprocessed_jobs(limit: int | None = None) -> list[Job]:
    """Return every job that isn't already in ``scored_jobs`` or ``skipped_jobs``.

    Unlike ``load_unscored_recent_jobs`` there is no time window — this is used
    by ``score-all`` to drain the unprocessed backlog after a re-hydration or
    chain-health fix. ``limit`` caps the result set (useful for partial runs).
    """
    client = get_client()
    try:
        scored_ids = {
            row["job_id"]
            for row in getattr(
                client.table("scored_jobs").select("job_id").execute(),
                "data",
                [],
            )
            or []
        }
        skipped_ids = {
            row["job_id"]
            for row in getattr(
                client.table("skipped_jobs").select("job_id").execute(),
                "data",
                [],
            )
            or []
        }
    except Exception as e:
        log.error("db.load_all_unprocessed_jobs.processed_fetch_failed", error=str(e))
        raise

    processed = scored_ids | skipped_ids
    out: list[Job] = []
    try:
        page_size = 1000
        offset = 0
        while True:
            q = (
                client.table("jobs")
                .select("*")
                .order("discovered_at", desc=True)
                .range(offset, offset + page_size - 1)
            )
            result = q.execute()
            rows = getattr(result, "data", []) or []
            if not rows:
                break
            for row in rows:
                if row["id"] in processed:
                    continue
                out.append(Job.model_validate(row))
                if limit is not None and len(out) >= limit:
                    return out
            if len(rows) < page_size:
                break
            offset += page_size
    except Exception as e:
        log.error("db.load_all_unprocessed_jobs.jobs_fetch_failed", error=str(e))
        raise
    return out


def load_scored_jobs_by_ids(scored_job_ids: list[str]) -> list[ScoredJob]:
    """Load ``scored_jobs`` rows and attach their ``Job`` payload."""
    if not scored_job_ids:
        return []
    client = get_client()
    out: list[ScoredJob] = []
    try:
        for i in range(0, len(scored_job_ids), 200):
            chunk = scored_job_ids[i : i + 200]
            result = (
                client.table("scored_jobs")
                .select("*")
                .in_("id", chunk)
                .execute()
            )
            rows = getattr(result, "data", []) or []
            job_ids = [row["job_id"] for row in rows]
            jobs_by_id = {str(j.id): j for j in load_jobs_by_ids(job_ids)}
            for row in rows:
                sj = ScoredJob.model_validate(row)
                sj.job = jobs_by_id.get(str(sj.job_id))
                out.append(sj)
    except Exception as e:
        log.error(
            "db.load_scored_jobs_by_ids.failed",
            count=len(scored_job_ids),
            error=str(e),
        )
        raise
    return out


def load_unnotified_applications(limit: int | None = None) -> list[Application]:
    """Return applications ready for Telegram delivery.

    Criteria: ``status='suggested'`` AND ``notified_at IS NULL``. Eagerly
    attaches the matching ``ScoredJob`` (with its ``Job`` populated), so the
    notifier can format a message without additional round-trips.
    """
    client = get_client()
    try:
        q = (
            client.table("applications")
            .select("*")
            .eq("status", "suggested")
            .is_("notified_at", "null")
            .order("updated_at", desc=False)
        )
        if limit is not None:
            q = q.limit(limit)
        result = q.execute()
    except Exception as e:
        log.error("db.load_unnotified_applications.failed", error=str(e))
        raise

    rows = getattr(result, "data", []) or []
    if not rows:
        return []

    scored_ids = [row["scored_job_id"] for row in rows]
    scored_by_id = {str(sj.id): sj for sj in load_scored_jobs_by_ids(scored_ids)}

    out: list[Application] = []
    for row in rows:
        app = Application.model_validate(row)
        app.scored_job = scored_by_id.get(str(app.scored_job_id))
        out.append(app)
    return out


def mark_application_notified(application_id: str) -> None:
    """Stamp ``applications.notified_at = NOW()`` for one row. Idempotent."""
    client = get_client()
    try:
        (
            client.table("applications")
            .update({"notified_at": datetime.now(timezone.utc).isoformat()})
            .eq("id", application_id)
            .execute()
        )
    except Exception as e:
        log.error(
            "db.mark_application_notified.failed",
            app_id=application_id,
            error=str(e),
        )
        raise


def start_run() -> str:
    client = get_client()
    try:
        result = client.table("runs").insert({"status": "running"}).execute()
        return _one(result, "insert runs")["id"]
    except Exception as e:
        log.error("db.start_run.failed", error=str(e))
        raise


def finish_run(run_id: str, stats: dict[str, Any]) -> None:
    """Update a run row with end-of-run stats.

    ``stats`` keys should mirror ``runs`` columns (``jobs_fetched``,
    ``jobs_new``, ``jobs_scored``, ``jobs_notified``, ``errors``, ``status``).
    """
    client = get_client()
    payload: dict[str, Any] = {
        "finished_at": datetime.now(timezone.utc).isoformat(),
        **stats,
    }
    try:
        client.table("runs").update(payload).eq("id", run_id).execute()
    except Exception as e:
        log.error("db.finish_run.failed", run_id=run_id, error=str(e))
        raise


def record_llm_call(
    *,
    run_id: str | None,
    stage: str,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    latency_ms: int,
    success: bool,
    error: str | None = None,
) -> None:
    """Insert one row into ``llm_calls``. Called from the chain after every
    provider attempt. Swallow DB-side errors at the caller (telemetry must
    never break the scoring path)."""
    client = get_client()
    payload: dict[str, Any] = {
        "run_id": run_id,
        "stage": stage,
        "provider": provider,
        "model": model,
        "input_tokens": int(input_tokens or 0),
        "output_tokens": int(output_tokens or 0),
        "latency_ms": int(latency_ms or 0),
        "success": bool(success),
        "error": error,
    }
    try:
        client.table("llm_calls").insert(payload).execute()
    except Exception as e:
        log.warning(
            "db.record_llm_call.failed",
            provider=provider,
            model=model,
            error=str(e),
        )
        raise
