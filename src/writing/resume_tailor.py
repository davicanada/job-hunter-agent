"""Tailor Davi's resume content for a specific job via the LLM.

Guardrail: the LLM is instructed to *select and rephrase*, never to invent.
We defend that rule programmatically — every returned highlight is fuzzy-
matched back to an entry in ``profile.experience[].highlights``. If overlap
drops below 70 percent of the profile source tokens, we discard the LLM
result for that run and fall back to a deterministic keyword-ranked pick.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from src.models.job import Job
from src.utils.llm import chat
from src.utils.logger import get_logger

log = get_logger(__name__)

_TAILOR_PROMPT_PATH = Path("data/prompts/resume_tailor.txt")
_PROMPT_DESCRIPTION_LIMIT = 3000
_DRIFT_THRESHOLD = 0.70
_FALLBACK_HIGHLIGHTS = 5
_WORD_RE = re.compile(r"[a-zA-Z0-9]+")


class SelectedHighlight(BaseModel):
    company: str
    title: str
    text: str


class TailoredResume(BaseModel):
    summary: str
    selected_highlights: list[SelectedHighlight] = Field(default_factory=list)
    selected_projects: list[str] = Field(default_factory=list)
    keywords_added: list[str] = Field(default_factory=list)


def _load_prompt_template() -> str:
    return _TAILOR_PROMPT_PATH.read_text(encoding="utf-8")


def _job_payload_for_prompt(job: Job) -> dict[str, Any]:
    return {
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "description": (job.description or "")[:_PROMPT_DESCRIPTION_LIMIT],
        "employment_type": job.employment_type,
    }


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _WORD_RE.findall(text or "")}


def _flatten_profile_highlights(
    profile: dict,
) -> list[dict[str, Any]]:
    """Return every profile highlight with its owning company + title."""
    out: list[dict[str, Any]] = []
    for exp in profile.get("experience", []):
        for hl in exp.get("highlights", []):
            out.append(
                {
                    "company": exp.get("company", ""),
                    "title": exp.get("title", ""),
                    "summary": hl.get("summary", ""),
                    "tags": hl.get("tags", []),
                }
            )
    return out


def _best_match(
    llm_text: str, profile_entries: list[dict[str, Any]]
) -> tuple[dict[str, Any] | None, float]:
    """Pick the profile highlight with highest token-overlap with ``llm_text``.

    Returns the entry and an overlap ratio in ``[0, 1]`` computed against the
    profile source's token set (so dropping tokens is cheap but adding new
    ones is what we penalise).
    """
    llm_tokens = _tokens(llm_text)
    if not llm_tokens:
        return None, 0.0
    best: dict[str, Any] | None = None
    best_score = 0.0
    for entry in profile_entries:
        profile_tokens = _tokens(entry["summary"])
        if not profile_tokens:
            continue
        overlap = len(llm_tokens & profile_tokens) / len(profile_tokens)
        if overlap > best_score:
            best_score = overlap
            best = entry
    return best, best_score


def _fallback_highlights(
    job: Job, profile: dict
) -> list[SelectedHighlight]:
    """Rank every profile highlight by raw keyword overlap with the job text."""
    entries = _flatten_profile_highlights(profile)
    job_text = f"{job.title}\n{job.description or ''}"
    job_tokens = _tokens(job_text)
    ranked: list[tuple[float, dict[str, Any]]] = []
    for entry in entries:
        tags = entry.get("tags") or []
        bucket = entry["summary"] + " " + " ".join(str(t) for t in tags)
        score = len(_tokens(bucket) & job_tokens)
        ranked.append((score, entry))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    top = [entry for _, entry in ranked[:_FALLBACK_HIGHLIGHTS]]
    return [
        SelectedHighlight(
            company=entry["company"], title=entry["title"], text=entry["summary"]
        )
        for entry in top
    ]


def _resolve_selected_highlights(
    llm_strings: list[str],
    profile: dict,
) -> tuple[list[SelectedHighlight], bool]:
    """Map each LLM string back to its owning profile entry.

    Returns ``(highlights, drift_detected)``. On drift we return an empty list
    and the caller should use ``_fallback_highlights``.
    """
    profile_entries = _flatten_profile_highlights(profile)
    resolved: list[SelectedHighlight] = []
    drift = False
    for text in llm_strings:
        entry, score = _best_match(text, profile_entries)
        if entry is None or score < _DRIFT_THRESHOLD:
            log.warning(
                "resume_tailor.drift_detected",
                overlap=round(score, 2),
                snippet=text[:160],
            )
            drift = True
            break
        resolved.append(
            SelectedHighlight(
                company=entry["company"],
                title=entry["title"],
                text=text,
            )
        )
    return resolved, drift


async def tailor_resume_content(
    profile: dict,
    job: Job,
    track: str,
) -> TailoredResume:
    """Call the LLM and return a validated ``TailoredResume``.

    If the LLM drifts (invents highlights) or the JSON fails to validate, we
    fall back to a deterministic, no-invention selection built from the
    profile + job keywords. The summary is then a canned one-liner so the
    .docx still renders — the cover letter keeps the bespoke tone.
    """
    template = _load_prompt_template()
    profile_json = json.dumps(profile, ensure_ascii=False)
    job_json = json.dumps(_job_payload_for_prompt(job), ensure_ascii=False)
    prompt = (
        template.replace("{profile_json}", profile_json)
        .replace("{job_json}", job_json)
        .replace("{track}", track or "other")
    )

    try:
        raw = await chat(
            [{"role": "user", "content": prompt}],
            temperature=0.3,
            json_mode=True,
            max_tokens=1500,
        )
    except Exception as e:  # noqa: BLE001
        log.error(
            "resume_tailor.llm_failed", job_id=str(job.id), error=str(e)
        )
        return TailoredResume(
            summary=profile.get("personal", {}).get("name", "")
            + " — data analyst with automation + BI focus.",
            selected_highlights=_fallback_highlights(job, profile),
            selected_projects=[
                p["name"] for p in profile.get("projects", [])[:3]
            ],
            keywords_added=[],
        )

    if not isinstance(raw, dict):
        log.error(
            "resume_tailor.llm_unexpected_type",
            job_id=str(job.id),
            got_type=type(raw).__name__,
        )
        raw = {}

    summary = str(
        raw.get("summary")
        or "Data analyst focused on SQL, Python, and BI automation."
    )
    llm_highlights = [str(s) for s in raw.get("selected_highlights", []) if s]
    selected_projects = [
        str(p) for p in raw.get("selected_projects", []) if p
    ]
    keywords_added = [str(k) for k in raw.get("keywords_added", []) if k]

    highlights, drift = _resolve_selected_highlights(llm_highlights, profile)
    if drift or not highlights:
        log.info("resume_tailor.using_fallback", job_id=str(job.id))
        highlights = _fallback_highlights(job, profile)

    # Validate project names against profile to strip anything fabricated.
    known_projects = {p["name"] for p in profile.get("projects", [])}
    selected_projects = [p for p in selected_projects if p in known_projects]
    if not selected_projects:
        selected_projects = [
            p["name"] for p in profile.get("projects", [])[:3]
        ]

    try:
        return TailoredResume(
            summary=summary,
            selected_highlights=highlights,
            selected_projects=selected_projects,
            keywords_added=keywords_added,
        )
    except ValidationError as e:
        log.error(
            "resume_tailor.validation_failed",
            job_id=str(job.id),
            error=str(e),
        )
        return TailoredResume(
            summary=summary,
            selected_highlights=_fallback_highlights(job, profile),
            selected_projects=[
                p["name"] for p in profile.get("projects", [])[:3]
            ],
            keywords_added=[],
        )
