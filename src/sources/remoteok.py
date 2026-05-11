"""RemoteOK adapter. JSON feed at https://remoteok.com/api."""
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


def _config() -> dict[str, Any]:
    for s in SOURCES:
        if s.get("name") == "remoteok":
            return dict(s)
    return {}


class RemoteOKAdapter(JobSourceAdapter):
    source_name = "remoteok"

    async def fetch(self) -> list[Job]:
        cfg = _config()
        url = cfg.get("url", "https://remoteok.com/api")
        ua = cfg.get("user_agent", "JobHunterAgent/1.0 (personal job search tool)")
        headers = {"User-Agent": ua, "Accept": "application/json"}

        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, headers=headers) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                payload = resp.json()
        except Exception as e:
            log.error("sources.remoteok.fetch_failed", error=str(e))
            return []

        if not isinstance(payload, list):
            log.warning("sources.remoteok.unexpected_schema", type=type(payload).__name__)
            return []

        # RemoteOK's first element is a legal notice, not a job.
        items = [item for item in payload if isinstance(item, dict) and item.get("id")]
        log.info("sources.remoteok.raw", count=len(items))

        jobs: list[Job] = []
        for item in items:
            try:
                job = self._normalize(item)
            except Exception as e:
                log.warning(
                    "sources.remoteok.normalize_failed",
                    item_id=item.get("id"),
                    error=str(e),
                )
                continue
            if job is None:
                continue
            jobs.append(job)

        log.info("sources.remoteok.kept", count=len(jobs))
        return jobs

    def _normalize(self, item: dict[str, Any]) -> Job | None:
        title = item.get("position") or item.get("title") or ""
        tags = item.get("tags") or []
        description_html = item.get("description", "")
        description = clean_html(description_html)

        if not is_data_relevant(title, tags, description):
            return None

        location = item.get("location", "") or ""
        allows_target_region = is_in_target_region(location, description, tags)
        if allows_target_region is False:
            return None

        company = item.get("company") or "Unknown"
        raw_id = str(item["id"])
        url = (
            item.get("url")
            or item.get("apply_url")
            or f"https://remoteok.com/remote-jobs/{raw_id}"
        )

        salary_min = item.get("salary_min") or None
        salary_max = item.get("salary_max") or None
        salary_currency = "USD" if (salary_min or salary_max) else None

        posted_at: datetime | None = None
        if item.get("date"):
            try:
                posted_at = dtparser.parse(item["date"])
            except (ValueError, TypeError):
                posted_at = None

        return Job(
            source="remoteok",
            source_job_id=raw_id,
            external_id=Job.make_external_id("remoteok", raw_id, url),
            title=title.strip(),
            company=str(company).strip(),
            location=location or None,
            is_remote=True,
            allows_target_region=allows_target_region,
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=salary_currency,
            employment_type=None,
            description=description,
            url=url,
            posted_at=posted_at,
            raw_data=item,
        )
