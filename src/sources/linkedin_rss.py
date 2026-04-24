"""LinkedIn Jobs RSS adapter.

LinkedIn exposes an undocumented RSS endpoint on the public jobs search. It
is gently rate-limited and occasionally returns empty feeds when LinkedIn
decides the traffic looks automated — we treat that as a soft failure and
let the other sources carry the run.
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
        if s.get("name") == "linkedin_rss":
            return dict(s)
    return {}


def build_urls(
    template: str, queries: list[str], location: str
) -> list[tuple[str, str]]:
    """Return a list of ``(query, url)`` pairs with query + location url-encoded."""
    loc_enc = quote_plus(location or "")
    out: list[tuple[str, str]] = []
    for q in queries:
        q_enc = quote_plus(q)
        out.append((q, template.format(query=q_enc, location=loc_enc)))
    return out


class LinkedInRSSAdapter(JobSourceAdapter):
    source_name = "linkedin_rss"

    async def fetch(self) -> list[Job]:
        cfg = _config()
        template = cfg.get("url_template")
        queries = list(cfg.get("queries") or [])
        location = cfg.get("location") or ""
        if not template or not queries:
            log.warning("sources.linkedin_rss.no_config")
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
                    "sources.linkedin_rss.query_failed",
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
                        "sources.linkedin_rss.normalize_failed",
                        link=link,
                        error=str(e),
                    )
                    continue
                if job is not None:
                    jobs.append(job)

        log.info(
            "sources.linkedin_rss.raw",
            entries=total_entries,
            unique=len(seen_links),
        )
        log.info("sources.linkedin_rss.kept", count=len(jobs))
        return jobs

    async def _fetch_feed(
        self, client: httpx.AsyncClient, query: str, url: str
    ) -> list[dict[str, Any]]:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except Exception as e:
            log.error(
                "sources.linkedin_rss.http_failed",
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
                }
            )
        return entries

    def _normalize(self, item: dict[str, Any]) -> Job | None:
        title_raw = (item.get("title") or "").strip()
        description_raw = item.get("description", "")
        description = clean_html(description_raw)

        # LinkedIn RSS title format: "Data Analyst at Acme Corp · Toronto, ON"
        # — split on " at " (bounded by spaces) to pull company, fall back
        # gracefully.
        title = title_raw
        company = item.get("author") or "Unknown (LinkedIn)"
        if " at " in title_raw:
            job_title, _, rest = title_raw.partition(" at ")
            title = job_title.strip() or title_raw
            company_part = rest.split("·")[0].strip() if "·" in rest else rest.strip()
            if company_part:
                company = company_part

        location: str | None = None
        if "·" in title_raw:
            loc_part = title_raw.rsplit("·", 1)[-1].strip()
            if loc_part:
                location = loc_part

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
            source="linkedin_rss",
            source_job_id=None,
            external_id=Job.make_external_id("linkedin_rss", None, link),
            title=title,
            company=company.strip() or "Unknown (LinkedIn)",
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
