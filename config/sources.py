"""Job source configurations + filter keyword lists."""
from __future__ import annotations

from typing import Any, Literal, TypedDict

SourceKind = Literal["api_json", "rss", "rss_multi"]
SourceName = Literal[
    "remoteok",
    "wwr",
    "remotive",
    "working_nomads",
    "jobicy",
    "himalayas",
    "linkedin_rss",
    "indeed_rss",
]


class SourceConfig(TypedDict, total=False):
    name: SourceName
    type: SourceKind
    url: str
    enabled: bool
    user_agent: str
    extra_feeds: list[str]
    category_filter: list[str]
    query_params: dict[str, Any]
    # rss_multi
    url_template: str
    queries: list[str]
    location: str


SOURCES: list[SourceConfig] = [
    {
        "name": "remoteok",
        "type": "api_json",
        "url": "https://remoteok.com/api",
        "enabled": True,
        "user_agent": "JobHunterAgent/1.0 (personal job search tool)",
    },
    {
        "name": "wwr",
        "type": "rss",
        "url": "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss",
        "enabled": True,
        "extra_feeds": [
            "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss",
            "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
            "https://weworkremotely.com/remote-jobs.rss",
        ],
    },
    {
        "name": "remotive",
        "type": "api_json",
        "url": "https://remotive.com/api/remote-jobs",
        "enabled": True,
        "category_filter": ["software-dev", "data"],
    },
    {
        "name": "working_nomads",
        "type": "api_json",
        "url": "https://www.workingnomads.com/api/exposed_jobs/",
        "enabled": True,
    },
    {
        # Block 3.5: ``industry=data-analytics,dev`` caused 400s on every call.
        # We now filter for data relevance client-side via ``is_data_relevant``.
        "name": "jobicy",
        "type": "api_json",
        "url": "https://jobicy.com/api/v2/remote-jobs",
        "enabled": True,
        "query_params": {
            "count": 50,
            "geo": "canada,anywhere",
        },
    },
    {
        "name": "himalayas",
        "type": "api_json",
        "url": "https://himalayas.app/jobs/api",
        "enabled": True,
    },
    {
        # Disabled 2026-04-24: LinkedIn's public RSS endpoint returns empty
        # feeds (bot detection). Never produced a job in this project's
        # lifetime. Queries + f_E=1,2,3 filter are kept below so that if
        # routed through a proxy or the Talent Solutions API later, the
        # junior-targeting is already in place.
        "name": "linkedin_rss",
        "type": "rss_multi",
        "url_template": (
            "https://www.linkedin.com/jobs/search?"
            "keywords={query}&location={location}&f_TPR=r86400&f_WT=2"
            "&f_E=1,2,3&position=1&pageNum=0&format=rss"
        ),
        "enabled": False,
        "queries": [
            "Data Analyst",
            "Data Engineer",
            "Analytics Engineer",
            "BI Analyst",
            "Reporting Analyst",
            "Operations Analyst",
            "RPA Developer",
        ],
        "location": "Canada",
    },
    {
        # Disabled 2026-04-24: Indeed Canada RSS returns 403 Forbidden on
        # every call (UA-based bot detection). Config preserved for future
        # re-enablement with a proxy or the official Indeed Publisher API.
        "name": "indeed_rss",
        "type": "rss_multi",
        "url_template": "https://ca.indeed.com/rss?q={query}&l={location}&fromage=3&explvl=entry_level",
        "enabled": False,
        "queries": [
            "data analyst",
            "data engineer",
            "analytics engineer",
            "bi analyst",
            "reporting analyst",
            "operations analyst",
            "rpa developer",
        ],
        "location": "Remote",
    },
]


CANADA_FRIENDLY_KEYWORDS: list[str] = [
    "canada",
    "worldwide",
    "anywhere",
    "remote - canada",
    "north america",
    "americas",
    "global",
    "emea + americas",
]

CANADA_BLOCKED_KEYWORDS: list[str] = [
    "us only",
    "usa only",
    "united states only",
    "uk only",
    "europe only",
    "eu only",
    "us-based",
    "must be based in the us",
    "us citizens only",
    "uk residents",
]

DATA_RELEVANT_KEYWORDS: list[str] = [
    "data",
    "analyst",
    "analytics",
    "sql",
    "python",
    "power bi",
    "tableau",
    "etl",
    "bi ",
    "business intelligence",
    "reporting",
    "dashboard",
    "automation",
    "rpa",
    "data engineer",
    "ai engineer",
    "ml engineer",
    "machine learning",
]
