"""Indeed Canada RSS adapter.

One HTTP call per query against ``ca.indeed.com/rss``. fromage=3 limits to
postings created in the last three days. Indeed sometimes throttles or
returns HTML rather than RSS — soft-fail each query and let others proceed.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any
from urllib.parse import quote_plus

import feedparser
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
_USER_AGENT = (
    "Mozilla/5.0 (JobHunterAgent/1.0; +https://github.com/davicanada22/"
    "job-hunter-agent)"
)


def _config() -> dict[str, Any]:
    for s in SOURCES:
        if s.get("name") == "indeed_rss":
            return dict(s)
    return {}


def build_urls(
    template: str, queries: list[str], location: str
) -> list[tuple[str, str]]:
    loc_enc = quote_plus(location or "")
    out: list[tuple[str, str]] = []
    for q in queries:
        q_enc = quote_plus(q)
        out.append((q, template.format(query=q_enc, location=loc_enc)))
    return out


class IndeedRSSAdapter(JobSourceAdapter):
    source_name = "indeed_rss"

    async def fetch(self) -> list[Job]:
        cfg = _config()
        template = cfg.get("url_template")
        queries = list(cfg.get("queries") or [])
        location = cfg.get("location") or ""
        if not template or not queries:
            log.warning("sources.indeed_rss.no_config")
            return []

        pairs = build_urls(template, queries, location)

        async with httpx.AsyncClient(
            timeout=TIMEOUT_SECONDS,
            headers={"User-Agent": _USER_AGENT, "Accept": "application/rss+xml"},
        ) as client:
            results = await asyncio.gather(
                *(self._fetch_feed(client, q, u) for q, u in pairs),
                return_exceptions=True,
            )

        seen_links: set[str] = set()
        jobs: list[Job] = []
        total_entries = 0
        for (query, _url), result in zip(pairs, results):
            if isinstance(result, Exception):
                log.error(
                    "sources.indeed_rss.query_failed",
                    query=query,
                    error=str(result),
                )
                continue
            total_entries += len(result)
            for item in result:
                link = item.get("link", "")
                if not link or link in seen_links:
                    continue
                seen_links.add(link)
                try:
                    job = self._normalize(item)
                except Exception as e:
                    log.warning(
                        "sources.indeed_rss.normalize_failed",
                        link=link,
                        error=str(e),
                    )
                    continue
                if job is not None:
                    jobs.append(job)

        log.info(
            "sources.indeed_rss.raw",
            entries=total_entries,
            unique=len(seen_links),
        )
        log.info("sources.indeed_rss.kept", count=len(jobs))
        return jobs

    async def _fetch_feed(
        self, client: httpx.AsyncClient, query: str, url: str
    ) -> list[dict[str, Any]]:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except Exception as e:
            log.error(
                "sources.indeed_rss.http_failed",
                query=query,
                error=str(e),
            )
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
                    "author": getattr(e, "author", ""),
                    "source": getattr(e, "source", ""),
                }
            )
        return entries

    def _normalize(self, item: dict[str, Any]) -> Job | None:
        # Indeed RSS title format: "Data Analyst - Acme Corp - Remote"
        title_raw = (item.get("title") or "").strip()
        description_raw = item.get("description", "")
        description = clean_html(description_raw)

        title = title_raw
        company = "Unknown (Indeed)"
        location: str | None = None
        parts = [p.strip() for p in title_raw.split(" - ") if p.strip()]
        if len(parts) >= 2:
            title = parts[0]
            company = parts[1]
            if len(parts) >= 3:
                location = parts[2]
        elif len(parts) == 1:
            title = parts[0]

        if not is_data_relevant(title, [], description):
            return None

        allows_canada = is_canada_friendly(location, description, tags=[])
        if allows_canada is False:
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
            source="indeed_rss",
            source_job_id=None,
            external_id=Job.make_external_id("indeed_rss", None, link),
            title=title,
            company=company or "Unknown (Indeed)",
            location=location,
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
