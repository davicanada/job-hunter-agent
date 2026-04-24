"""Tests for Block 2 — source adapters.

Unit tests cover the pure utility functions (no network).
Adapter smoke tests hit the real APIs and are gated by ``-m integration``.
"""
from __future__ import annotations

import pytest

from src.models.job import Job
from src.sources.base import (
    clean_html,
    hash_url,
    is_canada_friendly,
    is_data_relevant,
    parse_salary,
)

# ---------------------------------------------------------------------------
# is_canada_friendly
# ---------------------------------------------------------------------------
def test_is_canada_friendly_worldwide():
    assert is_canada_friendly("Worldwide", None, None) is True


def test_is_canada_friendly_canada():
    assert is_canada_friendly("Canada only", None, None) is True


def test_is_canada_friendly_us_only_blocked():
    assert is_canada_friendly("US only", None, None) is False


def test_is_canada_friendly_unclear():
    assert is_canada_friendly("Remote", None, None) is None


def test_is_canada_friendly_blocked_beats_friendly():
    # "Worldwide" would match friendly, but "US only" is also present.
    assert (
        is_canada_friendly(
            "Remote worldwide",
            description="Must be based in the US",
            tags=[],
        )
        is False
    )


def test_is_canada_friendly_reads_description_and_tags():
    assert (
        is_canada_friendly(
            location="",
            description="Open to candidates across North America",
            tags=[],
        )
        is True
    )
    assert is_canada_friendly("", "", ["remote-canada"]) is True


# ---------------------------------------------------------------------------
# is_data_relevant
# ---------------------------------------------------------------------------
def test_is_data_relevant_positive():
    assert is_data_relevant("Data Analyst", []) is True
    assert is_data_relevant("Junior Python Developer", []) is True
    assert is_data_relevant("Operations Automation Engineer", []) is True


def test_is_data_relevant_tags_only():
    assert is_data_relevant("Solutions Engineer", ["sql", "etl"]) is True


def test_is_data_relevant_negative():
    assert is_data_relevant("Senior React Native Developer", []) is False
    assert is_data_relevant("UI/UX Designer", ["figma"]) is False


def test_is_data_relevant_description_fallback():
    # title+tags miss; description kicks in as fallback.
    assert (
        is_data_relevant(
            "Backend Engineer",
            [],
            "Build reporting pipelines and dashboards for our BI team.",
        )
        is True
    )


# ---------------------------------------------------------------------------
# parse_salary
# ---------------------------------------------------------------------------
def test_parse_salary_usd_k():
    assert parse_salary("$80k - $120k") == (80000, 120000, "USD")


def test_parse_salary_cad_full():
    assert parse_salary("CA$90,000 - CA$110,000") == (90000, 110000, "CAD")


def test_parse_salary_usd_full_number():
    assert parse_salary("USD 100,000") == (100000, None, "USD")


def test_parse_salary_with_plus():
    lo, hi, cur = parse_salary("CA$90,000+")
    assert lo == 90000
    assert hi is None
    assert cur == "CAD"


def test_parse_salary_range_without_currency():
    lo, hi, cur = parse_salary("90-110K")
    assert lo == 90000
    assert hi == 110000
    assert cur is None


def test_parse_salary_unparseable():
    assert parse_salary("competitive") == (None, None, None)
    assert parse_salary("") == (None, None, None)
    assert parse_salary(None) == (None, None, None)


# ---------------------------------------------------------------------------
# clean_html
# ---------------------------------------------------------------------------
def test_clean_html_strips_tags():
    html = "<p>Hello <strong>world</strong>!</p><br/>More&amp;text"
    out = clean_html(html)
    assert "<" not in out and ">" not in out
    assert "Hello" in out and "world" in out
    assert "&amp;" not in out
    assert "More&text" in out


def test_clean_html_decodes_entities():
    assert "<" in clean_html("&lt;")
    assert "&" in clean_html("&amp;")


def test_clean_html_truncates():
    big = "<p>" + ("a" * 10000) + "</p>"
    out = clean_html(big)
    assert len(out) <= 8001  # 8000 + ellipsis


def test_clean_html_empty():
    assert clean_html("") == ""
    assert clean_html(None) == ""


# ---------------------------------------------------------------------------
# hash_url
# ---------------------------------------------------------------------------
def test_hash_url_stable_and_unique():
    a = hash_url("https://example.com/jobs/1")
    b = hash_url("https://example.com/jobs/1")
    c = hash_url("https://example.com/jobs/2")
    assert a == b
    assert a != c
    assert len(a) == 16


# ---------------------------------------------------------------------------
# Adapter integration tests — hit the real network. Opt-in via `-m integration`.
# ---------------------------------------------------------------------------
pytestmark_default = []


def _assert_valid_sample(jobs: list, source_name: str) -> None:
    assert isinstance(jobs, list), f"{source_name} must return a list"
    if jobs:
        sample = jobs[0]
        assert isinstance(sample, Job), f"{source_name} returned non-Job element"
        assert sample.source == source_name
        assert sample.external_id
        assert sample.title
        assert sample.company
        assert sample.url


@pytest.mark.integration
@pytest.mark.asyncio
async def test_remoteok_fetch_returns_jobs():
    from src.sources.remoteok import RemoteOKAdapter

    jobs = await RemoteOKAdapter().fetch()
    _assert_valid_sample(jobs, "remoteok")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_wwr_fetch_returns_jobs():
    from src.sources.wwr import WWRAdapter

    jobs = await WWRAdapter().fetch()
    _assert_valid_sample(jobs, "wwr")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_remotive_fetch_returns_jobs():
    from src.sources.remotive import RemotiveAdapter

    jobs = await RemotiveAdapter().fetch()
    _assert_valid_sample(jobs, "remotive")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_working_nomads_fetch_returns_jobs():
    from src.sources.working_nomads import WorkingNomadsAdapter

    jobs = await WorkingNomadsAdapter().fetch()
    _assert_valid_sample(jobs, "working_nomads")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_jobicy_fetch_returns_jobs():
    from src.sources.jobicy import JobicyAdapter

    jobs = await JobicyAdapter().fetch()
    _assert_valid_sample(jobs, "jobicy")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_himalayas_fetch_returns_jobs():
    from src.sources.himalayas import HimalayasAdapter

    jobs = await HimalayasAdapter().fetch()
    _assert_valid_sample(jobs, "himalayas")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_linkedin_rss_fetch_returns_jobs():
    from src.sources.linkedin_rss import LinkedInRSSAdapter

    jobs = await LinkedInRSSAdapter().fetch()
    _assert_valid_sample(jobs, "linkedin_rss")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_indeed_rss_fetch_returns_jobs():
    from src.sources.indeed_rss import IndeedRSSAdapter

    jobs = await IndeedRSSAdapter().fetch()
    _assert_valid_sample(jobs, "indeed_rss")


# ---------------------------------------------------------------------------
# RSS multi URL construction (no network)
# ---------------------------------------------------------------------------
def test_linkedin_build_urls_encodes_spaces():
    from src.sources.linkedin_rss import build_urls

    template = (
        "https://www.linkedin.com/jobs/search?keywords={query}&location={location}"
        "&f_TPR=r86400&f_WT=2&position=1&pageNum=0&format=rss"
    )
    pairs = build_urls(template, ["Data Analyst"], "Canada")
    assert len(pairs) == 1
    query, url = pairs[0]
    assert query == "Data Analyst"
    assert "keywords=Data+Analyst" in url
    assert "location=Canada" in url
    assert "f_WT=2" in url


def test_linkedin_build_urls_multiple_queries():
    from src.sources.linkedin_rss import build_urls

    pairs = build_urls(
        "https://host/?q={query}&l={location}",
        ["SQL Developer", "Data Engineer"],
        "Remote",
    )
    assert [p[0] for p in pairs] == ["SQL Developer", "Data Engineer"]
    assert "q=SQL+Developer" in pairs[0][1]
    assert "q=Data+Engineer" in pairs[1][1]


def test_indeed_build_urls_encodes_location():
    from src.sources.indeed_rss import build_urls

    pairs = build_urls(
        "https://ca.indeed.com/rss?q={query}&l={location}&fromage=3",
        ["data analyst"],
        "Remote",
    )
    _, url = pairs[0]
    assert "q=data+analyst" in url
    assert "l=Remote" in url
    assert "fromage=3" in url


# ---------------------------------------------------------------------------
# Jobicy params no longer contain the bad ``industry`` key
# ---------------------------------------------------------------------------
def test_jobicy_config_has_no_industry_param():
    from config.sources import SOURCES

    jobicy = next((s for s in SOURCES if s.get("name") == "jobicy"), None)
    assert jobicy is not None
    params = jobicy.get("query_params") or {}
    assert "industry" not in params
    assert params.get("geo") == "canada,anywhere"


# ---------------------------------------------------------------------------
# fetcher registry knows about the new sources
# ---------------------------------------------------------------------------
def test_fetcher_registry_knows_new_sources():
    from src.sources.fetcher import _ADAPTER_REGISTRY

    assert "linkedin_rss" in _ADAPTER_REGISTRY
    assert "indeed_rss" in _ADAPTER_REGISTRY
