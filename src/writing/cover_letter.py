"""Cover-letter generator.

Pre-detects the posting language from the job description (EN/FR) so the LLM
receives a hard directive rather than having to guess. Enforces a word budget
after the response — if the model runs over 280 words we re-prompt once,
capped to a single retry so the pipeline can't loop on a chatty model.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from src.models.job import Job, ScoredJob
from src.utils.llm import chat
from src.utils.logger import get_logger

log = get_logger(__name__)

_COVER_LETTER_PROMPT_PATH = Path("data/prompts/cover_letter.txt")
_FRENCH_MARKERS: tuple[str, ...] = (
    "nous",
    "vous",
    "notre",
    "entreprise",
    "équipe",
    "développeur",
    "développeuse",
    "données",
)
_FRENCH_WORD_RATIO_THRESHOLD = 0.20
_MAX_WORDS = 280
_RETRY_WORD_CAP = 230
_WORD_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ']+")


def detect_language(description: str | None) -> str:
    """Return ``'fr'`` if the first 500 chars read as French, else ``'en'``.

    Heuristic: ratio of French-only marker words to total words in the
    leading 500 characters must exceed 20 percent. The marker list is
    intentionally small and biased toward words that don't exist in
    English (``équipe``, ``développeur``, ``données``) plus a few heavy
    function words (``nous``, ``vous``, ``notre``).
    """
    if not description:
        return "en"
    head = description[:500].lower()
    words = _WORD_RE.findall(head)
    if not words:
        return "en"
    french_hits = sum(1 for w in words if w in _FRENCH_MARKERS)
    ratio = french_hits / len(words)
    return "fr" if ratio > _FRENCH_WORD_RATIO_THRESHOLD else "en"


def count_words(text: str) -> int:
    return len(_WORD_RE.findall(text or ""))


def _load_prompt_template() -> str:
    return _COVER_LETTER_PROMPT_PATH.read_text(encoding="utf-8")


def _job_payload_for_prompt(job: Job) -> dict[str, Any]:
    return {
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "description": (job.description or "")[:3000],
        "employment_type": job.employment_type,
    }


def _render_prompt(
    template: str,
    profile: dict,
    job: Job,
    scored_job: ScoredJob,
    language: str,
) -> str:
    profile_json = json.dumps(profile, ensure_ascii=False)
    job_json = json.dumps(_job_payload_for_prompt(job), ensure_ascii=False)
    out = (
        template.replace("{profile_json}", profile_json)
        .replace("{job_json}", job_json)
        .replace("{why_match}", scored_job.why_match or "")
        .replace("{watch_out}", scored_job.watch_out or "")
    )
    directive = (
        f"\n\nLanguage: write the entire letter in {'French' if language == 'fr' else 'English'}."
    )
    return out + directive


async def generate_cover_letter(
    profile: dict,
    job: Job,
    scored_job: ScoredJob,
) -> str:
    """Return the plain-text cover letter. Empty string on hard failure."""
    template = _load_prompt_template()
    language = detect_language(job.description)
    prompt = _render_prompt(template, profile, job, scored_job, language)

    try:
        text = await chat(
            [{"role": "user", "content": prompt}],
            temperature=0.4,
            json_mode=False,
            max_tokens=800,
        )
    except Exception as e:  # noqa: BLE001
        log.error(
            "cover_letter.llm_failed", job_id=str(job.id), error=str(e)
        )
        return ""

    if not isinstance(text, str):
        log.error(
            "cover_letter.unexpected_type",
            job_id=str(job.id),
            got_type=type(text).__name__,
        )
        return ""
    text = text.strip()

    if count_words(text) > _MAX_WORDS:
        log.info(
            "cover_letter.over_budget_retry",
            job_id=str(job.id),
            words=count_words(text),
        )
        retry_prompt = (
            prompt
            + f"\n\nPrevious output was too long ({count_words(text)} words)."
            f" Rewrite in under {_RETRY_WORD_CAP} words. Keep the same"
            " language, structure, and content hierarchy."
        )
        try:
            text2 = await chat(
                [{"role": "user", "content": retry_prompt}],
                temperature=0.3,
                json_mode=False,
                max_tokens=800,
            )
            if isinstance(text2, str) and text2.strip():
                text = text2.strip()
        except Exception as e:  # noqa: BLE001
            log.warning(
                "cover_letter.retry_failed",
                job_id=str(job.id),
                error=str(e),
            )

    return text
