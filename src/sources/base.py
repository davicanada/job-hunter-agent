"""Base adapter class + shared utility functions for all job sources."""
from __future__ import annotations

import hashlib
import html
import re
from abc import ABC, abstractmethod

from config.sources import (
    DATA_RELEVANT_KEYWORDS,
    TARGET_REGION_BLOCKED_KEYWORDS,
    TARGET_REGION_KEYWORDS,
)
from src.models.job import Job

MAX_DESCRIPTION_CHARS = 8000


class JobSourceAdapter(ABC):
    """Base adapter. Each source subclasses this and implements fetch()."""

    source_name: str = ""

    @abstractmethod
    async def fetch(self) -> list[Job]:
        """Fetch jobs from source and return normalized Job models.

        Should NOT raise on partial failure — log and return what it got.
        """
        ...


def is_in_target_region(
    location: str | None,
    description: str | None,
    tags: list[str] | None,
) -> bool | None:
    """True if the posting is in scope for the target regions (Canada, USA,
    Europe), False if explicitly limited to a region we exclude, None if unclear.

    Blocked keywords beat friendly keywords (e.g. "Remote worldwide, India only"
    → False). Postings limited to a single target region (US only, EU only)
    are accepted here — auth_status is the LLM scorer's job, not this filter.
    """
    blob_parts: list[str] = []
    if location:
        blob_parts.append(location)
    if description:
        blob_parts.append(description)
    if tags:
        blob_parts.extend(str(t) for t in tags)
    blob = " ".join(blob_parts).lower()
    if not blob:
        return None

    blocked = any(kw in blob for kw in TARGET_REGION_BLOCKED_KEYWORDS)
    if blocked:
        return False
    friendly = any(kw in blob for kw in TARGET_REGION_KEYWORDS)
    if friendly:
        return True
    return None


def is_data_relevant(
    title: str | None,
    tags: list[str] | None,
    description: str | None = None,
) -> bool:
    """True if any DATA_RELEVANT_KEYWORDS substring appears in title or tags (case-insensitive).

    Description is a weaker fallback: used only when title+tags have no hit.
    """
    title_blob = (title or "").lower()
    tags_blob = " ".join(str(t) for t in (tags or [])).lower()
    primary = f"{title_blob} {tags_blob}"
    if any(kw in primary for kw in DATA_RELEVANT_KEYWORDS):
        return True
    if description:
        desc_blob = description.lower()
        return any(kw in desc_blob for kw in DATA_RELEVANT_KEYWORDS)
    return False


_SALARY_CURRENCY_HINTS: list[tuple[str, str]] = [
    ("cad", "CAD"),
    ("ca$", "CAD"),
    ("c$", "CAD"),
    ("usd", "USD"),
    ("us$", "USD"),
    ("eur", "EUR"),
    ("€", "EUR"),
    ("gbp", "GBP"),
    ("£", "GBP"),
    ("$", "USD"),
]

_SALARY_NUM_RE = re.compile(
    r"""
    (?P<num>\d[\d,]*)           # 80 or 80,000 or 100000
    (?:\.\d+)?                   # optional decimal
    \s*(?P<suffix>[kK])?         # optional k/K
    """,
    re.VERBOSE,
)


def _to_int(num: str, suffix: str | None) -> int:
    cleaned = num.replace(",", "")
    value = int(float(cleaned))
    if suffix and suffix.lower() == "k":
        value *= 1000
    return value


def parse_salary(text: str | None) -> tuple[int | None, int | None, str | None]:
    """Best-effort salary parser for strings like '$80k - $120k', 'USD 100,000', 'CA$90,000+'.

    Returns (min, max, currency). Any field may be None if not recoverable.
    """
    if not text:
        return (None, None, None)
    raw = text.strip()
    if not raw:
        return (None, None, None)

    lower = raw.lower()
    currency: str | None = None
    for hint, code in _SALARY_CURRENCY_HINTS:
        if hint in lower:
            currency = code
            break

    matches = _SALARY_NUM_RE.findall(raw)
    parsed: list[tuple[int, str]] = []
    for num, suffix in matches:
        try:
            parsed.append((_to_int(num, suffix), suffix))
        except (ValueError, OverflowError):
            continue

    # If any number has a "k" suffix, propagate it to earlier suffix-less
    # numbers — this handles "80-120k" / "90-110K" where only the trailing
    # number is suffixed in the source text.
    if any(suffix for _, suffix in parsed):
        shared_k = next((s for _, s in parsed if s), None)
        if shared_k:
            normalized: list[int] = []
            for value, suffix in parsed:
                if not suffix and value < 1000:
                    value *= 1000
                normalized.append(value)
        else:
            normalized = [v for v, _ in parsed]
    else:
        normalized = [v for v, _ in parsed]

    # Filter obvious non-salary noise (years, small counts).
    plausible = [n for n in normalized if n >= 1000]
    if not plausible:
        return (None, None, currency)

    if len(plausible) == 1:
        return (plausible[0], None, currency)
    return (min(plausible[0], plausible[1]), max(plausible[0], plausible[1]), currency)


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def clean_html(raw: str | None) -> str:
    """Strip HTML tags, decode entities, collapse whitespace, truncate to 8000 chars."""
    if not raw:
        return ""
    text = _TAG_RE.sub(" ", raw)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text).strip()
    if len(text) > MAX_DESCRIPTION_CHARS:
        text = text[:MAX_DESCRIPTION_CHARS].rstrip() + "…"
    return text


def hash_url(url: str) -> str:
    """sha256(url) — first 16 chars. Used when source has no stable ID."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
