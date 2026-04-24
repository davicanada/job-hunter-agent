"""Pipeline entry point.

Block 2 wired up fetch → dedupe → persist.
Block 3 adds score → write. Block 3.5 adds ``rescore`` which reconciles
prefilter-skipped jobs against the current prefilter and runs scoring on
anything newly accepted. Block 4 appends Telegram delivery — every mode
runs ``notify_all`` after the writer so fresh matches get pushed the same
cron cycle, and a ``notify`` mode re-sends any backlog.

CLI:
    python -m src.main            # full cycle (fetch + score + write + notify)
    python -m src.main fetch      # fetch only (Block 2 behaviour)
    python -m src.main score      # score recent unscored jobs, no new fetch
    python -m src.main score-all  # score every unprocessed job (no time window)
    python -m src.main rescore    # reconcile prefilter-skipped rows + score them
    python -m src.main notify     # send any unnotified applications via Telegram
    python -m src.main providers  # print LLM provider chain + in-process status
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from pprint import pprint
from typing import Any

from config.settings import settings
from src.db.client import (
    finish_run,
    load_all_unprocessed_jobs,
    load_scored_jobs_by_ids,
    load_unscored_recent_jobs,
    persist_new_jobs,
    start_run,
)
from src.notify.telegram import notify_all
from src.scoring.scorer import ScoringResult, reconcile_skipped_jobs, score_jobs
from src.sources.fetcher import fetch_all_sources
from src.utils.llm import get_chain, get_chain_status
from src.utils.llm_telemetry import llm_context
from src.utils.logger import get_logger
from src.writing.writer import WriteResult, write_all_matching

log = get_logger(__name__)

_PROFILE_PATH = Path("data/profile.json")


def _load_profile() -> dict:
    return json.loads(_PROFILE_PATH.read_text(encoding="utf-8"))


async def run_fetch_cycle() -> dict[str, Any]:
    """One cycle of: fetch → dedupe → persist new jobs. Returns stats dict."""
    run_id = start_run()
    log.info("main.fetch_started", run_id=run_id)

    result = await fetch_all_sources()

    try:
        new_ids, duplicates_skipped = persist_new_jobs(result.jobs)
    except Exception as e:  # noqa: BLE001
        log.error("main.persist_failed", error=str(e))
        finish_run(
            run_id,
            stats={
                "jobs_fetched": result.total_fetched,
                "jobs_new": 0,
                "errors": {**result.errors, "persist": str(e)},
                "status": "failed",
            },
        )
        raise

    status = "success" if not result.errors else "partial"
    stats = {
        "jobs_fetched": result.total_fetched,
        "jobs_new": len(new_ids),
        "jobs_scored": 0,
        "jobs_notified": 0,
        "errors": result.errors or None,
        "status": status,
    }
    finish_run(run_id, stats=stats)

    out = {
        "mode": "fetch",
        "run_id": run_id,
        "total_fetched": result.total_fetched,
        "total_unique": result.total_unique,
        "per_source": result.per_source,
        "errors": result.errors,
        "inserted_new": len(new_ids),
        "duplicates_skipped": duplicates_skipped,
        "status": status,
    }
    log.info("main.fetch_finished", **out)
    return out


async def _score_and_write(
    run_id: str,
    job_ids: list[str],
) -> tuple[ScoringResult, WriteResult]:
    """Shared scoring + writing stage used by full + score-only modes. The
    ``llm_context(run_id=...)`` wrapper tags every downstream LLM call with
    this run so ``llm_calls`` rows can be grouped by run."""
    if not job_ids:
        return ScoringResult(), WriteResult()

    with llm_context(run_id=run_id):
        scoring = await score_jobs(job_ids)

        scored_jobs = load_scored_jobs_by_ids(scoring.scored_job_ids)
        profile = _load_profile()
        writing = await write_all_matching(
            scored_jobs=scored_jobs,
            run_id=run_id,
            min_score=settings.min_score_to_notify,
            profile=profile,
        )
    return scoring, writing


def _merge_status(fetch_errors: dict, scoring_errors: dict, write_errors: dict) -> str:
    if fetch_errors or scoring_errors or write_errors:
        return "partial"
    return "success"


async def _notify_stage(run_id: str) -> dict:
    """Run Telegram notify as a soft stage — crashes here don't abort the run."""
    try:
        return await notify_all()
    except Exception as e:  # noqa: BLE001
        log.error("main.notify_stage.failed", run_id=run_id, error=str(e))
        return {"total": 0, "sent": 0, "failed": 0, "error": str(e)}


async def run_full_cycle() -> dict[str, Any]:
    """Fetch → persist → score → write. Block 4 will append Telegram + error
    reporting on top of this."""
    run_id = start_run()
    log.info("main.full_started", run_id=run_id)

    try:
        fetch_result = await fetch_all_sources()
    except Exception as e:  # noqa: BLE001
        log.error("main.full.fetch_failed", error=str(e))
        finish_run(
            run_id,
            stats={"errors": {"fetch": str(e)}, "status": "failed"},
        )
        raise

    try:
        new_ids, duplicates_skipped = persist_new_jobs(fetch_result.jobs)
    except Exception as e:  # noqa: BLE001
        log.error("main.full.persist_failed", error=str(e))
        finish_run(
            run_id,
            stats={
                "jobs_fetched": fetch_result.total_fetched,
                "jobs_new": 0,
                "errors": {**fetch_result.errors, "persist": str(e)},
                "status": "failed",
            },
        )
        raise

    try:
        scoring, writing = await _score_and_write(run_id, new_ids)
    except Exception as e:  # noqa: BLE001
        log.error("main.full.score_write_failed", error=str(e))
        finish_run(
            run_id,
            stats={
                "jobs_fetched": fetch_result.total_fetched,
                "jobs_new": len(new_ids),
                "errors": {
                    **fetch_result.errors,
                    "score_write": str(e),
                },
                "status": "failed",
            },
        )
        raise

    notify = await _notify_stage(run_id)

    status = _merge_status(fetch_result.errors, scoring.errors, writing.errors)
    stats = {
        "jobs_fetched": fetch_result.total_fetched,
        "jobs_new": len(new_ids),
        "jobs_scored": scoring.llm_scored,
        "jobs_notified": notify.get("sent", 0),
        "errors": {
            **(fetch_result.errors or {}),
            **({f"score:{k}": v for k, v in scoring.errors.items()}),
            **({f"write:{k}": v for k, v in writing.errors.items()}),
            **({"notify": notify["error"]} if notify.get("error") else {}),
        }
        or None,
        "status": status,
    }
    finish_run(run_id, stats=stats)

    summary = {
        "mode": "full",
        "run_id": run_id,
        "total_fetched": fetch_result.total_fetched,
        "per_source": fetch_result.per_source,
        "inserted_new": len(new_ids),
        "duplicates_skipped": duplicates_skipped,
        "prefiltered_out": scoring.prefiltered_out,
        "llm_scored": scoring.llm_scored,
        "by_verdict": scoring.by_verdict,
        "by_track": scoring.by_track,
        "materials_written": writing.written,
        "materials_failed": writing.failed,
        "outputs": writing.outputs,
        "notify": notify,
        "errors": {
            "fetch": fetch_result.errors or None,
            "scoring": scoring.errors or None,
            "writing": writing.errors or None,
        },
        "status": status,
    }
    log.info("main.full_finished", run_id=run_id, status=status)
    return summary


async def run_scoring_only() -> dict[str, Any]:
    """Score any un-scored recent jobs (last 48h) and write materials for the
    qualifiers. Useful for re-running scoring without re-fetching."""
    run_id = start_run()
    log.info("main.score_only_started", run_id=run_id)

    try:
        pending = load_unscored_recent_jobs(hours=48)
    except Exception as e:  # noqa: BLE001
        log.error("main.score_only.load_failed", error=str(e))
        finish_run(run_id, stats={"errors": {"load": str(e)}, "status": "failed"})
        raise

    job_ids = [str(j.id) for j in pending if j.id]
    try:
        scoring, writing = await _score_and_write(run_id, job_ids)
    except Exception as e:  # noqa: BLE001
        log.error("main.score_only.score_write_failed", error=str(e))
        finish_run(
            run_id,
            stats={"errors": {"score_write": str(e)}, "status": "failed"},
        )
        raise

    notify = await _notify_stage(run_id)

    status = _merge_status({}, scoring.errors, writing.errors)
    stats = {
        "jobs_fetched": 0,
        "jobs_new": 0,
        "jobs_scored": scoring.llm_scored,
        "jobs_notified": notify.get("sent", 0),
        "errors": {
            **({f"score:{k}": v for k, v in scoring.errors.items()}),
            **({f"write:{k}": v for k, v in writing.errors.items()}),
            **({"notify": notify["error"]} if notify.get("error") else {}),
        }
        or None,
        "status": status,
    }
    finish_run(run_id, stats=stats)

    summary = {
        "mode": "score",
        "run_id": run_id,
        "pending_jobs": len(job_ids),
        "prefiltered_out": scoring.prefiltered_out,
        "llm_scored": scoring.llm_scored,
        "by_verdict": scoring.by_verdict,
        "by_track": scoring.by_track,
        "materials_written": writing.written,
        "materials_failed": writing.failed,
        "outputs": writing.outputs,
        "notify": notify,
        "errors": {
            "scoring": scoring.errors or None,
            "writing": writing.errors or None,
        },
        "status": status,
    }
    log.info("main.score_only_finished", run_id=run_id, status=status)
    return summary


BULK_SCORE_BATCH_CAP = 20


async def run_score_all_unprocessed() -> dict[str, Any]:
    """Score every job that isn't yet in ``scored_jobs`` or ``skipped_jobs``.

    Capped at ``BULK_SCORE_BATCH_CAP`` per invocation — free-tier Gemini +
    Groq TPD budgets can't drain a full backlog in one run. Re-invoke after
    quotas reset to process the next chunk.
    """
    run_id = start_run()
    log.info("main.score_all_started", run_id=run_id)

    try:
        pending = load_all_unprocessed_jobs(limit=BULK_SCORE_BATCH_CAP)
    except Exception as e:  # noqa: BLE001
        log.error("main.score_all.load_failed", error=str(e))
        finish_run(run_id, stats={"errors": {"load": str(e)}, "status": "failed"})
        raise

    job_ids = [str(j.id) for j in pending if j.id]
    log.info("main.score_all.loaded", pending=len(job_ids))

    try:
        scoring, writing = await _score_and_write(run_id, job_ids)
    except Exception as e:  # noqa: BLE001
        log.error("main.score_all.score_write_failed", error=str(e))
        finish_run(
            run_id,
            stats={"errors": {"score_write": str(e)}, "status": "failed"},
        )
        raise

    notify = await _notify_stage(run_id)

    status = _merge_status({}, scoring.errors, writing.errors)
    stats = {
        "jobs_fetched": 0,
        "jobs_new": 0,
        "jobs_scored": scoring.llm_scored,
        "jobs_notified": notify.get("sent", 0),
        "errors": {
            **({f"score:{k}": v for k, v in scoring.errors.items()}),
            **({f"write:{k}": v for k, v in writing.errors.items()}),
            **({"notify": notify["error"]} if notify.get("error") else {}),
        }
        or None,
        "status": status,
    }
    finish_run(run_id, stats=stats)

    summary = {
        "mode": "score-all",
        "run_id": run_id,
        "pending_jobs": len(job_ids),
        "prefiltered_out": scoring.prefiltered_out,
        "llm_scored": scoring.llm_scored,
        "by_verdict": scoring.by_verdict,
        "by_track": scoring.by_track,
        "materials_written": writing.written,
        "materials_failed": writing.failed,
        "outputs": writing.outputs,
        "notify": notify,
        "errors": {
            "scoring": scoring.errors or None,
            "writing": writing.errors or None,
        },
        "status": status,
    }
    log.info("main.score_all_finished", run_id=run_id, status=status)
    return summary


async def run_rescore_cycle() -> dict[str, Any]:
    """Reconcile prefilter-skipped jobs against the current prefilter, then
    score + write materials for anything that now qualifies. Use this after
    tightening or relaxing prefilter rules, or after a prompt change that
    changes what "junior-friendly" looks like."""
    run_id = start_run()
    log.info("main.rescore_started", run_id=run_id)

    try:
        reconcile = reconcile_skipped_jobs()
    except Exception as e:  # noqa: BLE001
        log.error("main.rescore.reconcile_failed", error=str(e))
        finish_run(
            run_id,
            stats={"errors": {"reconcile": str(e)}, "status": "failed"},
        )
        raise

    job_ids = list(reconcile.requeued_job_ids)
    try:
        scoring, writing = await _score_and_write(run_id, job_ids)
    except Exception as e:  # noqa: BLE001
        log.error("main.rescore.score_write_failed", error=str(e))
        finish_run(
            run_id,
            stats={"errors": {"score_write": str(e)}, "status": "failed"},
        )
        raise

    notify = await _notify_stage(run_id)

    status = _merge_status({}, scoring.errors, writing.errors)
    stats = {
        "jobs_fetched": 0,
        "jobs_new": 0,
        "jobs_scored": scoring.llm_scored,
        "jobs_notified": notify.get("sent", 0),
        "errors": {
            **({f"score:{k}": v for k, v in scoring.errors.items()}),
            **({f"write:{k}": v for k, v in writing.errors.items()}),
            **({"notify": notify["error"]} if notify.get("error") else {}),
        }
        or None,
        "status": status,
    }
    finish_run(run_id, stats=stats)

    summary = {
        "mode": "rescore",
        "run_id": run_id,
        "examined": reconcile.examined,
        "requeued": reconcile.requeued,
        "still_rejected": reconcile.still_rejected,
        "llm_scored": scoring.llm_scored,
        "by_verdict": scoring.by_verdict,
        "by_track": scoring.by_track,
        "materials_written": writing.written,
        "materials_failed": writing.failed,
        "outputs": writing.outputs,
        "notify": notify,
        "errors": {
            "scoring": scoring.errors or None,
            "writing": writing.errors or None,
        },
        "status": status,
    }
    log.info("main.rescore_finished", run_id=run_id, status=status)
    return summary


async def run_notify_only() -> dict[str, Any]:
    """Send any unnotified applications without re-fetching or re-scoring."""
    run_id = start_run()
    log.info("main.notify_started", run_id=run_id)
    notify = await _notify_stage(run_id)
    status = "success" if not notify.get("error") and notify.get("failed", 0) == 0 else "partial"
    finish_run(
        run_id,
        stats={
            "jobs_fetched": 0,
            "jobs_new": 0,
            "jobs_scored": 0,
            "jobs_notified": notify.get("sent", 0),
            "errors": {"notify": notify["error"]} if notify.get("error") else None,
            "status": status,
        },
    )
    summary = {"mode": "notify", "run_id": run_id, "status": status, **notify}
    log.info("main.notify_finished", run_id=run_id, status=status)
    return summary


def _print_provider_table() -> None:
    """Print the LLM provider fallback chain with in-process status.

    Priority is taken from ``chain.providers`` order (1 = tried first);
    status comes from ``get_chain_status()`` which keys on
    ``"<name>:<model>"`` — same key the chain uses internally so multiple
    Groq models stay distinguishable. No network calls.
    """
    chain = get_chain()
    status = get_chain_status()
    rows = []
    for idx, provider in enumerate(chain.providers, start=1):
        key = f"{provider.name}:{provider.model}"
        rows.append((idx, provider.name, provider.model, status.get(key, "unknown")))

    name_w = max((len(r[1]) for r in rows), default=len("provider"))
    model_w = max((len(r[2]) for r in rows), default=len("model"))
    name_w = max(name_w, len("provider"))
    model_w = max(model_w, len("model"))

    header = f"{'#':>2}  {'provider':<{name_w}}  {'model':<{model_w}}  status"
    sep = "-" * len(header)
    print("LLM provider chain (priority order — 1 = tried first):")
    print(header)
    print(sep)
    for idx, name, model, st in rows:
        print(f"{idx:>2}  {name:<{name_w}}  {model:<{model_w}}  {st}")


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "full"
    # Accept either positional ("rescore") or flag form ("--rescore").
    if mode.startswith("--"):
        mode = mode.lstrip("-")
    if mode == "providers":
        _print_provider_table()
        return
    if mode == "fetch":
        stats = asyncio.run(run_fetch_cycle())
    elif mode == "score":
        stats = asyncio.run(run_scoring_only())
    elif mode in ("score-all", "score_all"):
        stats = asyncio.run(run_score_all_unprocessed())
    elif mode == "rescore":
        stats = asyncio.run(run_rescore_cycle())
    elif mode == "notify":
        stats = asyncio.run(run_notify_only())
    elif mode == "full":
        stats = asyncio.run(run_full_cycle())
    else:
        print(
            f"Unknown mode: {mode!r}. Expected one of: full, fetch, score, score-all, rescore, notify, providers."
        )
        sys.exit(2)
    pprint(stats)


if __name__ == "__main__":
    main()
