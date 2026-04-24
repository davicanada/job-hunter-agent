"""Himalayas adapter. JSON API at https://himalayas.app/jobs/api."""
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
    is_canada_friendly,
    is_data_relevant,
)
from src.utils.logger import get_logger

log = get_logger(__name__)

TIMEOUT_SECONDS = 20.0

_EMPLOYMENT_TYPE_MAP: dict[str, str] = {
    "full-time": "full-time",
    "full_time": "full-time",
    "fulltime": "full-time",
    "part-time": "part-time",
    "part_time": "part-time",
    "contract": "contract",
    "freelance": "contract",
    "internship": "internship",
    "temporary": "temporary",
}


def _config() -> dict[str, Any]:
    for s in SOURCES:
        if s.get("name") == "himalayas":
            return dict(s)
    return {}


class HimalayasAdapter(JobSourceAdapter):
    source_name = "himalayas"

    async def fetch(self) -> list[Job]:
        cfg = _config()
        url = cfg.get("url", "https://himalayas.app/jobs/api")
        headers = {
            "User-Agent": "JobHunterAgent/1.0",
            "Accept": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, headers=headers) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                payload = resp.json()
        except Exception as e:
            log.error("sources.himalayas.fetch_failed", error=str(e))
            return []

        entries: list[Any]
        if isinstance(payload, dict):
            entries = payload.get("jobs") or []
        elif isinstance(payload, list):
            entries = payload
        else:
            log.warning(
                "sources.himalayas.unexpected_schema", type=type(payload).__name__
            )
            return []

        if not isinstance(entries, list):
            log.warning("sources.himalayas.jobs_not_list")
            return []

        log.info("sources.himalayas.raw", count=len(entries))

        jobs: list[Job] = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            raw_id = str(item.get("id") or item.get("guid") or "")
            try:
                job = self._normalize(item, raw_id)
            except Exception as e:
                log.warning(
                    "sources.himalayas.normalize_failed",
                    item_id=raw_id,
                    error=str(e),
                )
                continue
            if job is not None:
                jobs.append(job)

        log.info("sources.himalayas.kept", count=len(jobs))
        return jobs

    def _normalize(self, item: dict[str, Any], raw_id: str) -> Job | None:
        title = item.get("title") or item.get("jobTitle") or ""
        categories = item.get("categories") or []
        if isinstance(categories, str):
            categories = [categories]
        description = clean_html(item.get("description", ""))

        if not is_data_relevant(title, list(categories), description):
            return None

        location = item.get("jobLocation") or item.get("locationRestrictions") or ""
        if isinstance(location, list):
            location = ", ".join(str(x) for x in location)
        allows_canada = is_canada_friendly(location, description, list(categories))
        if allows_canada is False:
            return None

        url = (
            item.get("applicationLink")
            or item.get("url")
            or item.get("jobSlug")
            or ""
        )
        if not url:
            return None
        if url.startswith("/"):
            url = f"https://himalayas.app{url}"

        salary_min = item.get("salaryMin") or None
        salary_max = item.get("salaryMax") or None
        try:
            salary_min = int(salary_min) if salary_min else None
            salary_max = int(salary_max) if salary_max else None
        except (ValueError, TypeError):
            salary_min = salary_max = None
        salary_currency = item.get("currency") or None

        employment_type_raw = (item.get("employmentType") or "").lower()
        employment_type = _EMPLOYMENT_TYPE_MAP.get(employment_type_raw)

        posted_at: datetime | None = None
        if item.get("pubDate") or item.get("publishedDate"):
            try:
                posted_at = dtparser.parse(
                    item.get("pubDate") or item.get("publishedDate")
                )
            except (ValueError, TypeError):
                posted_at = None

        source_job_id = raw_id if raw_id else None
        external_id = Job.make_external_id("himalayas", source_job_id, url)

        return Job(
            source="himalayas",
            source_job_id=source_job_id,
            external_id=external_id,
            title=str(title).strip(),
            company=(item.get("companyName") or item.get("company") or "Unknown").strip(),
            location=str(location) or None,
            is_remote=True,
            allows_canada=allows_canada,
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=salary_currency,
            employment_type=employment_type,
            description=description,
            url=url,
            posted_at=posted_at,
            raw_data=item,
        )
