"""Working Nomads adapter. JSON API at https://www.workingnomads.com/api/exposed_jobs/."""
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
    hash_url,
    is_canada_friendly,
    is_data_relevant,
)
from src.utils.logger import get_logger

log = get_logger(__name__)

TIMEOUT_SECONDS = 20.0


def _config() -> dict[str, Any]:
    for s in SOURCES:
        if s.get("name") == "working_nomads":
            return dict(s)
    return {}


class WorkingNomadsAdapter(JobSourceAdapter):
    source_name = "working_nomads"

    async def fetch(self) -> list[Job]:
        cfg = _config()
        url = cfg.get("url", "https://www.workingnomads.com/api/exposed_jobs/")
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
            log.error("sources.working_nomads.fetch_failed", error=str(e))
            return []

        if not isinstance(payload, list):
            log.warning(
                "sources.working_nomads.unexpected_schema", type=type(payload).__name__
            )
            return []

        log.info("sources.working_nomads.raw", count=len(payload))

        jobs: list[Job] = []
        seen_urls: set[str] = set()
        for item in payload:
            if not isinstance(item, dict):
                continue
            link = item.get("url", "")
            if not link or link in seen_urls:
                continue
            seen_urls.add(link)
            try:
                job = self._normalize(item, link)
            except Exception as e:
                log.warning(
                    "sources.working_nomads.normalize_failed",
                    link=link,
                    error=str(e),
                )
                continue
            if job is not None:
                jobs.append(job)

        log.info("sources.working_nomads.kept", count=len(jobs))
        return jobs

    def _normalize(self, item: dict[str, Any], link: str) -> Job | None:
        title = item.get("title", "")
        tags_raw = item.get("tags", "")
        tags: list[str] = []
        if isinstance(tags_raw, str):
            tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
        elif isinstance(tags_raw, list):
            tags = [str(t).strip() for t in tags_raw]

        description = clean_html(item.get("description", ""))

        if not is_data_relevant(title, tags, description):
            return None

        location = item.get("location") or ""
        allows_canada = is_canada_friendly(location, description, tags)
        if allows_canada is False:
            return None

        posted_at: datetime | None = None
        if item.get("pub_date"):
            try:
                posted_at = dtparser.parse(item["pub_date"])
            except (ValueError, TypeError):
                posted_at = None

        external_id = Job.make_external_id("working_nomads", None, link)

        return Job(
            source="working_nomads",
            source_job_id=None,
            external_id=external_id,
            title=title.strip(),
            company=(item.get("company_name") or "Unknown").strip(),
            location=location or None,
            is_remote=True,
            allows_canada=allows_canada,
            salary_min=None,
            salary_max=None,
            salary_currency=None,
            employment_type=None,
            description=description,
            url=link,
            posted_at=posted_at,
            raw_data=item,
        )
