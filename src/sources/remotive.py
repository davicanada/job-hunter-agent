"""Remotive adapter. JSON API at https://remotive.com/api/remote-jobs."""
from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
from dateutil import parser as dtparser

from config.sources import SOURCES
from src.models.job import Job
from src.sources.base import (
    JobSourceAdapter,
    clean_html,
    is_data_relevant,
    is_in_target_region,
    parse_salary,
)
from src.utils.logger import get_logger

log = get_logger(__name__)

TIMEOUT_SECONDS = 20.0

_EMPLOYMENT_TYPE_MAP: dict[str, str] = {
    "full_time": "full-time",
    "contract": "contract",
    "part_time": "part-time",
    "freelance": "contract",
    "internship": "internship",
    "temporary": "temporary",
}


def _config() -> dict[str, Any]:
    for s in SOURCES:
        if s.get("name") == "remotive":
            return dict(s)
    return {}


class RemotiveAdapter(JobSourceAdapter):
    source_name = "remotive"

    async def fetch(self) -> list[Job]:
        cfg = _config()
        base_url = cfg.get("url", "https://remotive.com/api/remote-jobs")
        categories: list[str] = list(cfg.get("category_filter") or ["software-dev", "data"])

        headers = {
            "User-Agent": "JobHunterAgent/1.0",
            "Accept": "application/json",
        }

        all_jobs_raw: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, headers=headers) as client:
            for category in categories:
                try:
                    resp = await client.get(base_url, params={"category": category})
                    resp.raise_for_status()
                    payload = resp.json()
                except Exception as e:
                    log.error(
                        "sources.remotive.fetch_failed",
                        category=category,
                        error=str(e),
                    )
                    continue
                entries = payload.get("jobs") if isinstance(payload, dict) else None
                if not isinstance(entries, list):
                    log.warning(
                        "sources.remotive.unexpected_schema", category=category
                    )
                    continue
                all_jobs_raw.extend(entries)

        log.info("sources.remotive.raw", count=len(all_jobs_raw))

        jobs: list[Job] = []
        seen_ids: set[str] = set()
        for item in all_jobs_raw:
            if not isinstance(item, dict):
                continue
            raw_id = str(item.get("id") or "")
            if not raw_id or raw_id in seen_ids:
                continue
            seen_ids.add(raw_id)
            try:
                job = self._normalize(item, raw_id)
            except Exception as e:
                log.warning(
                    "sources.remotive.normalize_failed",
                    item_id=raw_id,
                    error=str(e),
                )
                continue
            if job is not None:
                jobs.append(job)

        log.info("sources.remotive.kept", count=len(jobs))
        return jobs

    def _normalize(self, item: dict[str, Any], raw_id: str) -> Job | None:
        title = item.get("title", "")
        tags = item.get("tags") or []
        description = clean_html(item.get("description", ""))

        if not is_data_relevant(title, tags, description):
            return None

        location = item.get("candidate_required_location") or ""
        allows_target_region = is_in_target_region(location, description, tags)
        if allows_target_region is False:
            return None

        url = item.get("url") or ""
        if not url:
            return None

        salary_min, salary_max, salary_currency = parse_salary(item.get("salary"))

        employment_type_raw = (item.get("job_type") or "").lower()
        employment_type = _EMPLOYMENT_TYPE_MAP.get(employment_type_raw)

        posted_at: datetime | None = None
        if item.get("publication_date"):
            try:
                posted_at = dtparser.parse(item["publication_date"])
            except (ValueError, TypeError):
                posted_at = None

        return Job(
            source="remotive",
            source_job_id=raw_id,
            external_id=Job.make_external_id("remotive", raw_id, url),
            title=title.strip(),
            company=(item.get("company_name") or "Unknown").strip(),
            location=location or None,
            is_remote=True,
            allows_target_region=allows_target_region,
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=salary_currency,
            employment_type=employment_type,
            description=description,
            url=url,
            posted_at=posted_at,
            raw_data=item,
        )
