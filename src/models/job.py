"""Pydantic v2 models mirroring the Supabase schema."""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

JobSource = Literal[
    "remoteok", "wwr", "remotive", "working_nomads", "jobicy", "himalayas",
    "linkedin_rss", "indeed_rss",
]
Verdict = Literal["strong_match", "stretch", "skip"]
Track = Literal[
    "analytics_engineer", "ops_data_analyst", "automation_dev", "data_engineer", "other",
]
AuthStatus = Literal["ok_work_permit", "future_pr", "blocked_citizen_only", "unclear"]
EmploymentType = Literal["full-time", "part-time", "contract", "temporary", "internship"]
ApplicationStatus = Literal[
    "suggested", "applied", "skipped", "regenerate_requested",
    "interview", "rejected", "ghosted",
]
RunStatus = Literal["running", "success", "partial", "failed"]


class Job(BaseModel):
    """Mirror of the `jobs` table."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: UUID | None = None
    external_id: str
    source: JobSource
    source_job_id: str | None = None
    title: str
    company: str
    location: str | None = None
    is_remote: bool = True
    allows_canada: bool | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = None
    employment_type: EmploymentType | None = None
    description: str
    url: str
    posted_at: datetime | None = None
    discovered_at: datetime | None = None
    raw_data: dict[str, Any] | None = None

    @classmethod
    def make_external_id(cls, source: str, source_job_id: str | None, url: str) -> str:
        """Stable sha256 hash over (source, source_job_id or url)."""
        key = f"{source}:{source_job_id or url}"
        return hashlib.sha256(key.encode("utf-8")).hexdigest()


class ScoredJob(BaseModel):
    """Mirror of the `scored_jobs` table with optional embedded Job."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: UUID | None = None
    job_id: UUID
    score: int = Field(ge=0, le=100)
    verdict: Verdict
    track: Track | None = None
    why_match: str | None = None
    watch_out: str | None = None
    auth_status: AuthStatus | None = None
    scored_at: datetime | None = None
    model: str | None = None
    recency_bonus: int = 0
    age_days: int | None = None
    job: Job | None = None


class Application(BaseModel):
    """Mirror of the `applications` table."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: UUID | None = None
    scored_job_id: UUID
    status: ApplicationStatus = "suggested"
    resume_path: str | None = None
    cover_letter_path: str | None = None
    notes: str | None = None
    applied_at: datetime | None = None
    updated_at: datetime | None = None
    notified_at: datetime | None = None
    scored_job: ScoredJob | None = None


class Run(BaseModel):
    """Mirror of the `runs` table."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: UUID | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    jobs_fetched: int = 0
    jobs_new: int = 0
    jobs_scored: int = 0
    jobs_notified: int = 0
    errors: dict[str, Any] | None = None
    status: RunStatus = "running"


class FetchResult(BaseModel):
    """Aggregate output of one fetch cycle across all sources."""

    model_config = ConfigDict(extra="ignore")

    total_fetched: int = 0
    total_unique: int = 0
    per_source: dict[str, int] = Field(default_factory=dict)
    errors: dict[str, str] = Field(default_factory=dict)
    jobs: list[Job] = Field(default_factory=list)
