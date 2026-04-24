"""Filesystem helpers for the writing pipeline.

Keeps output naming predictable so re-runs overwrite instead of piling up,
and so filenames are always safe on Windows/macOS/Linux.
"""
from __future__ import annotations

import re
from pathlib import Path

from src.models.job import Job

OUTPUTS_ROOT = Path("data/outputs")
_SLUG_MAX = 40
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str, max_len: int = _SLUG_MAX) -> str:
    """lowercase, spaces/punctuation → underscore, trimmed to ``max_len``."""
    if not text:
        return "untitled"
    normalised = _SLUG_RE.sub("_", text.lower()).strip("_")
    if not normalised:
        return "untitled"
    if len(normalised) > max_len:
        normalised = normalised[:max_len].rstrip("_")
    return normalised


def make_run_output_dir(run_id: str) -> Path:
    """Create ``data/outputs/{run_id_short}/`` and return it."""
    short = str(run_id).split("-")[0] if run_id else "manual"
    out = OUTPUTS_ROOT / short
    out.mkdir(parents=True, exist_ok=True)
    return out


def make_job_output_paths(run_dir: Path, job: Job) -> tuple[Path, Path]:
    """Return ``(resume_path, cover_letter_path)`` for ``job``.

    Filename format: ``{company_slug}_{title_slug}_resume.docx`` and
    ``{company_slug}_{title_slug}_cover.docx``. Collisions overwrite — the
    caller decides whether to append more uniqueness (job_id suffix) if needed.
    """
    company = slugify(job.company)
    title = slugify(job.title)
    base = f"{company}_{title}"
    return run_dir / f"{base}_resume.docx", run_dir / f"{base}_cover.docx"
