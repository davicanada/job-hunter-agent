"""We Work Remotely (WWR) adapter. RSS feeds, not JSON."""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import feedparser
import httpx
from dateutil import parser as dtparser

from config.sources import SOURCES
from src.models.job import Job
from src.sources.base import (
    JobSourceAdapter,
    clean_html,
    hash_url,
    is_data_relevant,
    is_in_target_region,
)
from src.utils.logger import get_logger

log = get_logger(__name__)

TIMEOUT_SECONDS = 20.0


def _config() -> dict[str, Any]:
    for s in SOURCES:
        if s.get("name") == "wwr":
            return dict(s)
    return {}


class WWRAdapter(JobSourceAdapter):
    source_name = "wwr"

    async def fetch(self) -> list[Job]:
        cfg = _config()
        feeds = [cfg.get("url")] + list(cfg.get("extra_feeds", []))
        feeds = [f for f in feeds if f]

        async with httpx.AsyncClient(
            timeout=TIMEOUT_SECONDS,
            headers={"User-Agent": "JobHunterAgent/1.0"},
        ) as client:
            results = await asyncio.gather(
                *(self._fetch_feed(client, url) for url in feeds),
                return_exceptions=True,
            )

        seen_urls: set[str] = set()
        jobs: list[Job] = []
        total_entries = 0
        for feed_url, result in zip(feeds, results):
            if isinstance(result, Exception):
                log.error("sources.wwr.feed_failed", feed=feed_url, error=str(result))
                continue
            total_entries += len(result)
            for item in result:
                link = item.get("link", "")
                if not link or link in seen_urls:
                    continue
                seen_urls.add(link)
                try:
                    job = self._normalize(item)
                except Exception as e:
                    log.warning(
                        "sources.wwr.normalize_failed", link=link, error=str(e)
                    )
                    continue
                if job is None:
                    continue
                jobs.append(job)

        log.info("sources.wwr.raw", entries=total_entries, unique=len(seen_urls))
        log.info("sources.wwr.kept", count=len(jobs))
        return jobs

    async def _fetch_feed(self, client: httpx.AsyncClient, url: str) -> list[dict[str, Any]]:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except Exception as e:
            log.error("sources.wwr.http_failed", feed=url, error=str(e))
            return []
        parsed = feedparser.parse(resp.text)
        entries: list[dict[str, Any]] = []
        for e in parsed.entries:
            entries.append(
                {
                    "title": getattr(e, "title", ""),
                    "link": getattr(e, "link", ""),
                    "description": getattr(e, "description", "")
                    or getattr(e, "summary", ""),
                    "published": getattr(e, "published", ""),
                }
            )
        return entries

    def _normalize(self, item: dict[str, Any]) -> Job | None:
        title_raw = (item.get("title") or "").strip()
        description_raw = item.get("description", "")
        description = clean_html(description_raw)

        if ": " in title_raw:
            company, _, title = title_raw.partition(": ")
            company = company.strip() or "Unknown (WWR)"
            title = title.strip() or title_raw
        else:
            company = "Unknown (WWR)"
            title = title_raw

        if not is_data_relevant(title, [], description):
            return None

        allows_target_region = is_in_target_region(
            location=None, description=description, tags=[]
        )
        if allows_target_region is False:
            return None

        link = item.get("link", "")
        if not link:
            return None

        posted_at: datetime | None = None
        if item.get("published"):
            try:
                posted_at = dtparser.parse(item["published"])
            except (ValueError, TypeError):
                posted_at = None

        return Job(
            source="wwr",
            source_job_id=None,
            external_id=Job.make_external_id("wwr", None, link),
            title=title,
            company=company,
            location=None,
            is_remote=True,
            allows_target_region=allows_target_region,
            salary_min=None,
            salary_max=None,
            salary_currency=None,
            employment_type=None,
            description=description,
            url=link,
            posted_at=posted_at,
            raw_data=item,
        )
