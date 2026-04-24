"""Smoke tests: all modules import, data files parse, models instantiate."""
from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


# ---------------------------------------------------------------------------
# Imports: every source module must load under dummy env vars
# ---------------------------------------------------------------------------
def test_import_config_settings():
    import config.settings as s

    assert s.settings.supabase_url
    assert s.settings.groq_model == "llama-3.3-70b-versatile"


def test_import_config_sources():
    from config import sources

    assert isinstance(sources.SOURCES, list)


def test_import_src_utils_logger():
    from src.utils.logger import get_logger

    log = get_logger("test")
    assert log is not None


def test_import_src_utils_llm():
    from src.utils import llm  # noqa: F401

    assert callable(llm.chat)


def test_import_src_models_job():
    from src.models import job  # noqa: F401

    assert hasattr(job, "Job")
    assert hasattr(job, "ScoredJob")
    assert hasattr(job, "Application")


def test_import_src_db_client():
    from src.db import client  # noqa: F401

    assert callable(client.get_client)
    assert callable(client.insert_job)


def test_import_src_main():
    from src import main

    assert callable(main.main)


def test_import_stub_packages():
    import src.notify  # noqa: F401
    import src.scoring  # noqa: F401
    import src.sources  # noqa: F401
    import src.writing  # noqa: F401


# ---------------------------------------------------------------------------
# Data files
# ---------------------------------------------------------------------------
def test_profile_json_parses():
    profile = json.loads((DATA / "profile.json").read_text(encoding="utf-8"))
    assert profile["personal"]["name"] == "Davi Almeida"
    assert profile["personal"]["location"].startswith("Montreal")
    assert isinstance(profile["target_roles"], list)
    assert len(profile["target_roles"]) == 4
    assert profile["work_authorization"]["pr_status"] == "pr_approved_copr_pending"
    assert profile["work_authorization"]["current_permit_expires"] == "2026-11-19"
    assert any("French" in lang for lang in profile["languages"])


def test_profile_json_tracks_match_schema_literals():
    from src.models.job import Track

    profile = json.loads((DATA / "profile.json").read_text(encoding="utf-8"))
    valid = set(Track.__args__)
    for role in profile["target_roles"]:
        assert role["track"] in valid, f"Unknown track: {role['track']}"


def test_prompt_templates_load_and_have_placeholders():
    scorer = (DATA / "prompts" / "scorer.txt").read_text(encoding="utf-8")
    tailor = (DATA / "prompts" / "resume_tailor.txt").read_text(encoding="utf-8")
    cover = (DATA / "prompts" / "cover_letter.txt").read_text(encoding="utf-8")

    for placeholder in ("{profile_json}", "{job_json}"):
        assert placeholder in scorer
        assert placeholder in tailor
        assert placeholder in cover

    assert "{track}" in tailor
    assert "{why_match}" in cover
    assert "{watch_out}" in cover


def test_master_resume_file_exists():
    content = (DATA / "master_resume.md").read_text(encoding="utf-8")
    assert "Davi Almeida" in content
    assert len(content) > 500


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
def test_job_model_instantiates_with_defaults():
    from src.models.job import Job

    job = Job(
        external_id="abc123",
        source="remoteok",
        title="Junior Data Analyst",
        company="Acme",
        description="desc",
        url="https://example.com/job/1",
    )
    assert job.title == "Junior Data Analyst"
    assert job.is_remote is True
    assert job.salary_min is None
    assert job.id is None


def test_scored_job_model_instantiates():
    from src.models.job import ScoredJob

    sj = ScoredJob(
        job_id=uuid4(),
        score=88,
        verdict="strong_match",
        track="analytics_engineer",
        why_match="fits",
        watch_out="no dbt",
        auth_status="ok_work_permit",
    )
    assert sj.score == 88
    assert sj.verdict == "strong_match"


def test_application_model_defaults_to_suggested():
    from src.models.job import Application

    app = Application(scored_job_id=uuid4())
    assert app.status == "suggested"


def test_run_model_defaults():
    from src.models.job import Run

    r = Run()
    assert r.jobs_fetched == 0
    assert r.status == "running"


# ---------------------------------------------------------------------------
# Job.make_external_id
# ---------------------------------------------------------------------------
def test_make_external_id_is_deterministic():
    from src.models.job import Job

    a = Job.make_external_id("remoteok", "12345", "https://example.com/1")
    b = Job.make_external_id("remoteok", "12345", "https://example.com/1")
    assert a == b
    assert len(a) == 64  # sha256 hex length


def test_make_external_id_prefers_source_job_id():
    from src.models.job import Job

    a = Job.make_external_id("remoteok", "12345", "https://example.com/1")
    b = Job.make_external_id("remoteok", "12345", "https://example.com/2")
    assert a == b  # url is ignored when source_job_id is present


def test_make_external_id_falls_back_to_url():
    from src.models.job import Job

    a = Job.make_external_id("wwr", None, "https://example.com/1")
    b = Job.make_external_id("wwr", None, "https://example.com/1")
    c = Job.make_external_id("wwr", None, "https://example.com/2")
    assert a == b
    assert a != c


def test_make_external_id_differs_by_source():
    from src.models.job import Job

    a = Job.make_external_id("remoteok", "12345", "x")
    b = Job.make_external_id("remotive", "12345", "x")
    assert a != b
