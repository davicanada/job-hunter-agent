"""Heuristic pre-filter. Cheap rejection of obvious non-matches before the LLM.

Tightened 2026-04-25: the candidate targets only internship / entry-level /
junior roles, so stretch-band senior postings are pure noise — each one burns
an LLM call against scarce free-tier budgets and can leak into the notification
queue if it was scored by an older prompt. Hard rejects now cover every level
at or above mid in the title, explicit 3+ years requirements in the title or
description, senior role phrases in the description, paid-to-apply sources,
and the existing domain / stack / geography blockers.

Geography (2026-05-11): the target region was widened from Canada-only to
Canada / USA / Europe. ``allows_target_region`` is False only when the posting
is restricted to a region we exclude (APAC, LATAM, India only, etc.); the
LLM scorer decides on actual auth_status case-by-case.

Fields on ``PrefilterResult`` are preserved for scorer compatibility but
``seniority_hint`` and ``notes`` are always empty for accepted jobs.
"""
from __future__ import annotations

import re

from pydantic import BaseModel, Field

from config.sources import PAID_TO_APPLY_SOURCES
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
    "architect",
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

_LEVEL_TITLE_RE = re.compile(
    r"\b(?:data|analytics|bi|business intelligence|software|backend|front[-\s]?end|"
    r"full[-\s]?stack|python|sql|machine learning|ml|ai|automation|operations)?"
    r"\s*(?:analyst|engineer|developer|scientist|specialist|consultant|administrator)"
    r"\s+(?:ii|iii|iv|v|2|3|4|5)\b",
    re.IGNORECASE,
)

_DESCRIPTION_SENIORITY_RE = re.compile(
    r"\b(?:senior|sr\.?|staff|principal|lead|manager|mid[-\s]?level|"
    r"intermediate|director|vp|vice president|head of|chief|architect)\b"
    r"(?:[-\s]+level|\s+(?:data|analytics|business intelligence|bi|software|"
    r"backend|front[-\s]?end|full[-\s]?stack|python|sql|machine learning|ml|ai|"
    r"automation|operations|engineering|engineer|developer|analyst|scientist|"
    r"specialist|consultant|administrator|architect|role|position))",
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

# 3+ years is no longer worth scoring for this search. Ranges that begin at
# 0-2 years still pass, e.g. "0-2 years" or "1 to 3 years".
_YEARS_ANY_RE = re.compile(r"\b([3-9]|1[0-9])\+?\s*(?:years?|yrs?)\b", re.IGNORECASE)
_LOW_RANGE_BEFORE_RE = re.compile(r"\b[0-2]\s*(?:-|–|—|to)\s*$", re.IGNORECASE)
_YEARS_REQUIREMENT_HINT_RE = re.compile(
    r"\b(?:experience|professional|relevant|hands[-\s]?on|work|industry|"
    r"minimum|min\.?|at\s+least|requires?|requirement|must\s+have|need(?:s)?|"
    r"you\s+(?:have|bring)|looking\s+for|with)\b",
    re.IGNORECASE,
)


def _find_experience_requirement(
    text: str,
    *,
    require_hint: bool = True,
) -> re.Match[str] | None:
    for match in _YEARS_ANY_RE.finditer(text):
        before = text[max(0, match.start() - 20) : match.start()]
        if _LOW_RANGE_BEFORE_RE.search(before):
            continue

        context_start = max(0, match.start() - 90)
        context_end = min(len(text), match.end() + 90)
        context = text[context_start:context_end]
        if not require_hint or _YEARS_REQUIREMENT_HINT_RE.search(context):
            return match
    return None


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

    paid_reason = PAID_TO_APPLY_SOURCES.get(job.source)
    if paid_reason:
        return PrefilterResult(
            should_score=False,
            skip_reason=f"source requires paid job-seeker access: {paid_reason}",
        )

    if job.allows_target_region is False:
        return PrefilterResult(
            should_score=False,
            skip_reason="location: not in target regions (Canada/USA/Europe)",
        )

    hard_match = _HARD_TITLE_RE.search(title)
    if hard_match:
        return PrefilterResult(
            should_score=False,
            skip_reason=f"seniority mismatch: title contains '{hard_match.group(0)}'",
        )

    level_match = _LEVEL_TITLE_RE.search(title)
    if level_match:
        return PrefilterResult(
            should_score=False,
            skip_reason=f"seniority mismatch: title contains level '{level_match.group(0)}'",
        )

    years_match = _find_experience_requirement(title, require_hint=False)
    if years_match:
        return PrefilterResult(
            should_score=False,
            skip_reason=f"years in title: '{years_match.group(0)}'",
        )

    description_seniority = _DESCRIPTION_SENIORITY_RE.search(description)
    if description_seniority:
        return PrefilterResult(
            should_score=False,
            skip_reason=(
                "seniority mismatch: description contains "
                f"'{description_seniority.group(0)}'"
            ),
        )

    description_years = _find_experience_requirement(description)
    if description_years:
        return PrefilterResult(
            should_score=False,
            skip_reason=f"years in description: '{description_years.group(0)}'",
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
