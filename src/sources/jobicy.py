"""Jobicy adapter. JSON API at https://jobicy.com/api/v2/remote-jobs."""
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
)
from src.utils.logger import get_logger

log = get_logger(__name__)

TIMEOUT_SECONDS = 20.0

_EMPLOYMENT_TYPE_MAP: dict[str, str] = {
    "full time": "full-time",
    "full-time": "full-time",
    "fulltime": "full-time",
    "part time": "part-time",
    "part-time": "part-time",
    "contract": "contract",
    "contractor": "contract",
    "freelance": "contract",
    "internship": "internship",
    "temporary": "temporary",
}


def _config() -> dict[str, Any]:
    for s in SOURCES:
        if s.get("name") == "jobicy":
            return dict(s)
    return {}


class JobicyAdapter(JobSourceAdapter):
    source_name = "jobicy"

    async def fetch(self) -> list[Job]:
        cfg = _config()
        url = cfg.get("url", "https://jobicy.com/api/v2/remote-jobs")
        params = cfg.get("query_params") or {}
        headers = {
            "User-Agent": "JobHunterAgent/1.0",
            "Accept": "application/json",
        }

        payload: Any = None
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, headers=headers) as client:
                resp = await client.get(url, params=params)
                if resp.status_code == 400:
                    # Block 3.5: Jobicy rejects some query-param combinations
                    # with 400. Retry with no params at all and filter fully
                    # client-side via ``is_data_relevant``.
                    log.warning(
                        "sources.jobicy.retry_no_params",
                        status=400,
                        first_params=dict(params),
                    )
                    resp = await client.get(url)
                resp.raise_for_status()
                payload = resp.json()
        except Exception as e:
            log.error("sources.jobicy.fetch_failed", error=str(e))
            return []

        entries = payload.get("jobs") if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            log.warning("sources.jobicy.unexpected_schema")
            return []

        log.info("sources.jobicy.raw", count=len(entries))

        jobs: list[Job] = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            raw_id = str(item.get("id") or "")
            if not raw_id:
                continue
            try:
                job = self._normalize(item, raw_id)
            except Exception as e:
                log.warning(
                    "sources.jobicy.normalize_failed",
                    item_id=raw_id,
                    error=str(e),
                )
                continue
            if job is not None:
                jobs.append(job)

        log.info("sources.jobicy.kept", count=len(jobs))
        return jobs

    def _normalize(self, item: dict[str, Any], raw_id: str) -> Job | None:
        title = item.get("jobTitle", "")
        industries = item.get("jobIndustry") or []
        if isinstance(industries, str):
            industries = [industries]
        description = clean_html(item.get("jobDescription", ""))

        if not is_data_relevant(title, list(industries), description):
            return None

        job_geo = item.get("jobGeo") or ""
        allows_target_region = is_in_target_region(job_geo, description, list(industries))
        if allows_target_region is False:
            return None

        url = item.get("url") or ""
        if not url:
            return None

        salary_min = item.get("annualSalaryMin") or None
        salary_max = item.get("annualSalaryMax") or None
        try:
            salary_min = int(salary_min) if salary_min else None
            salary_max = int(salary_max) if salary_max else None
        except (ValueError, TypeError):
            salary_min = salary_max = None
        salary_currency = item.get("salaryCurrency") or None

        job_type_raw = item.get("jobType") or []
        employment_type: str | None = None
        if isinstance(job_type_raw, list) and job_type_raw:
            employment_type = _EMPLOYMENT_TYPE_MAP.get(str(job_type_raw[0]).lower())
        elif isinstance(job_type_raw, str):
            employment_type = _EMPLOYMENT_TYPE_MAP.get(job_type_raw.lower())

        posted_at: datetime | None = None
        if item.get("pubDate"):
            try:
                posted_at = dtparser.parse(item["pubDate"])
            except (ValueError, TypeError):
                posted_at = None

        return Job(
            source="jobicy",
            source_job_id=raw_id,
            external_id=Job.make_external_id("jobicy", raw_id, url),
            title=title.strip(),
            company=(item.get("companyName") or "Unknown").strip(),
            location=job_geo or None,
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
