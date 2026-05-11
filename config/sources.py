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


PAID_TO_APPLY_SOURCES: dict[SourceName, str] = {
    "wwr": "We Work Remotely now gates unlimited applications behind Pro",
    "remotive": "Remotive uses paid job-seeker access for its full remote-jobs database",
    "working_nomads": "Working Nomads uses a paid premium subscription for expanded job access",
}


SOURCES: list[SourceConfig] = [
    {
        "name": "remoteok",
        "type": "api_json",
        "url": "https://remoteok.com/api",
        "enabled": True,
        "user_agent": "JobHunterAgent/1.0 (personal job search tool)",
    },
    {
        # Disabled 2026-04-25: user hit a job-seeker paywall / Pro flow while
        # applying. Keep the adapter code around, but do not fetch WWR jobs.
        "name": "wwr",
        "type": "rss",
        "url": "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss",
        "enabled": False,
        "extra_feeds": [
            "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss",
            "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
            "https://weworkremotely.com/remote-jobs.rss",
        ],
    },
    {
        # Disabled 2026-04-25: paid job-seeker access is not compatible with
        # this pipeline's "send resume without subscriptions" constraint.
        "name": "remotive",
        "type": "api_json",
        "url": "https://remotive.com/api/remote-jobs",
        "enabled": False,
        "category_filter": ["software-dev", "data"],
    },
    {
        # Disabled 2026-04-25: Working Nomads promotes a paid job-seeker
        # subscription / premium access path, so avoid routing applications
        # there.
        "name": "working_nomads",
        "type": "api_json",
        "url": "https://www.workingnomads.com/api/exposed_jobs/",
        "enabled": False,
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
            "geo": "canada,usa,europe,anywhere",
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


TARGET_REGION_KEYWORDS: list[str] = [
    # Canada
    "canada",
    "remote - canada",
    # USA — multi-char only; bare "us" / "uk" / "eu" would create false
    # positives via substring match (e.g. "must", "duke", "queue").
    "united states",
    "usa",
    "u.s.",
    "u.s.a.",
    "us only",
    "us-based",
    "us based",
    "us citizens",
    "remote - us",
    "remote us",
    "remote (us)",
    # Europe / EMEA
    "europe",
    "european",
    "emea",
    "united kingdom",
    "uk only",
    "uk residents",
    "europe only",
    "eu only",
    "eu + uk",
    "ireland",
    "germany",
    "france",
    "netherlands",
    "spain",
    "portugal",
    "poland",
    "sweden",
    "denmark",
    "finland",
    "norway",
    "belgium",
    "italy",
    "remote - europe",
    "anywhere in europe",
    # Cross-region / global
    "worldwide",
    "anywhere",
    "north america",
    "americas",
    "global",
    "emea + americas",
    "latam + emea + americas",
]

# Restrictions that genuinely exclude all three target regions (Canada, USA,
# Europe). Postings limited to a single target region (e.g. "US only",
# "Europe only") are NOT blocked here — they're in scope and the LLM scorer
# decides on auth_status based on Davi's actual work authorization.
TARGET_REGION_BLOCKED_KEYWORDS: list[str] = [
    "india only",
    "apac only",
    "latam only",
    "latin america only",
    "brazil only",
    "mexico only",
    "australia only",
    "anz only",
    "singapore only",
    "japan only",
    "africa only",
    "philippines only",
    "must be based in india",
    "must reside in latin america",
]

DATA_RELEVANT_KEYWORDS: list[str] = [
    "data",
    "analyst",
    "analytics",
    "sql",
    "python",
    "power bi",
    "excel",
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
