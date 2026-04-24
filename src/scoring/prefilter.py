"""Heuristic pre-filter. Cheap rejection of obvious non-matches before the LLM.

Tightened 2026-04-24: the candidate targets only internship / entry-level /
junior roles, so stretch-band senior postings are pure noise — each one burns
an LLM call against scarce free-tier budgets. Hard rejects now cover every
level at or above mid: senior, sr, lead (any position), manager, mid-level,
intermediate, plus the existing staff / principal / director / VP /
head-of / chief. The soft ``seniority_hint`` path from Block 3.5 is gone —
nothing ambiguous survives this layer anymore.

Fields on ``PrefilterResult`` are preserved for scorer compatibility but
``seniority_hint`` and ``notes`` are always empty for accepted jobs.
"""
from __future__ import annotations

import re

from pydantic import BaseModel, Field

from src.models.job import Job

# -----------------------------------------------------------------------------
# Hard rejections (every level at or above mid)
# -----------------------------------------------------------------------------
SENIORITY_HARD_TITLES: tuple[str, ...] = (
    # Leadership & IC-senior
    "staff",
    "principal",
    "vp",
    "vice president",
    "director",
    "head of",
    "chief",
    # Senior / mid / lead / manager (promoted from soft-flag)
    "senior",
    "sr",
    "lead",
    "manager",
    "mid-level",
    "mid level",
    "intermediate",
)

# Anchor to word boundaries so "seniority" / "managerial" / "leadership" /
# "leading" / "usrv" don't produce false rejections. Right edge uses
# ``(?!\w)`` so "sr." also matches even though ``.`` isn't a word character
# that ``\b`` can anchor against.
_HARD_TITLE_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(k) for k in SENIORITY_HARD_TITLES) + r")(?!\w)",
    re.IGNORECASE,
)

DOMAIN_BLOCKLIST: tuple[str, ...] = (
    "registered nurse",
    "truck driver",
    "sales representative",
    "account executive",
    "customer success",
    "recruiter",
    "virtual assistant",
    "copywriter",
    "business development",
    "medical coder",
    "medical coding",
    "performance marketing",
    "crypto trader",
    "crypto trading",
    "learning and development",
)

STACK_MISMATCH_TITLES: tuple[str, ...] = (
    "react native",
    "ios developer",
    "android developer",
    "unity",
    "unreal",
    "game developer",
    "embedded",
    "firmware",
    "hardware engineer",
    "mechanical engineer",
)

# 7+ years in title is hard-reject. 5-6 years still passes — the LLM rubric
# caps those at 65 anyway, and the job description sometimes welcomes junior
# applicants despite a quoted experience range.
_YEARS_IN_TITLE_RE = re.compile(
    r"\b([7-9]|1[0-9])\+?\s*(years|yrs)", re.IGNORECASE
)


class PrefilterResult(BaseModel):
    should_score: bool
    skip_reason: str | None = None
    seniority_hint: str | None = None
    notes: list[str] = Field(default_factory=list)


def prefilter_job(job: Job, profile: dict) -> PrefilterResult:
    """Return a ``PrefilterResult``. Accepts only clearly junior-appropriate
    postings; everything at or above mid is hard-rejected."""
    title = (job.title or "").lower()
    description = (job.description or "").lower()

    if job.allows_canada is False:
        return PrefilterResult(
            should_score=False,
            skip_reason="location: not Canada-friendly",
        )

    hard_match = _HARD_TITLE_RE.search(title)
    if hard_match:
        return PrefilterResult(
            should_score=False,
            skip_reason=f"seniority mismatch: title contains '{hard_match.group(0)}'",
        )

    years_match = _YEARS_IN_TITLE_RE.search(title)
    if years_match:
        return PrefilterResult(
            should_score=False,
            skip_reason=f"years in title: '{years_match.group(0)}'",
        )

    for kw in DOMAIN_BLOCKLIST:
        if kw in title or kw in description:
            return PrefilterResult(
                should_score=False,
                skip_reason=f"domain blocklist: '{kw}'",
            )

    for kw in STACK_MISMATCH_TITLES:
        if kw in title:
            return PrefilterResult(
                should_score=False,
                skip_reason=f"stack mismatch: '{kw}'",
            )

    return PrefilterResult(should_score=True, skip_reason=None)
