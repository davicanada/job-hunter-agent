"""Tests for ``src/notify/telegram.py`` — pure Python, no network.

The HTTP calls are patched at the module-level (``send_message`` /
``send_document``) so the tests never hit api.telegram.org. DB helpers
(``load_unnotified_applications``, ``mark_application_notified``) are
patched inside ``src.notify.telegram``'s import namespace.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from src.models.job import Application, Job, ScoredJob


def _make_app(
    *,
    title: str = "Junior Data Analyst",
    company: str = "Acme Corp",
    score: int = 72,
    verdict: str = "stretch",
    track: str = "analytics_engineer",
    why_match: str = "Strong SQL and Power BI overlap with Davi's profile.",
    watch_out: str = "Tableau not Power BI; small gap.",
    url: str = "https://example.com/job/123",
    age_days: int | None = 1,
    resume_path: str | None = "/tmp/resume.docx",
    cover_letter_path: str | None = "/tmp/cover.docx",
) -> Application:
    job = Job(
        id=uuid4(),
        external_id="ext-123",
        source="remoteok",
        title=title,
        company=company,
        description="x",
        url=url,
        allows_canada=True,
    )
    sj = ScoredJob(
        id=uuid4(),
        job_id=job.id,
        score=score,
        verdict=verdict,
        track=track,
        why_match=why_match,
        watch_out=watch_out,
        auth_status="ok_work_permit",
        age_days=age_days,
        job=job,
    )
    return Application(
        id=uuid4(),
        scored_job_id=sj.id,
        status="suggested",
        resume_path=resume_path,
        cover_letter_path=cover_letter_path,
        scored_job=sj,
    )


# ---------------------------------------------------------------------------
# format_message
# ---------------------------------------------------------------------------
def test_format_message_includes_core_fields():
    from src.notify.telegram import format_message

    app = _make_app()
    msg = format_message(app)
    assert "Junior Data Analyst" in msg
    assert "Acme Corp" in msg
    assert "72" in msg
    assert "stretch" in msg
    assert "analytics_engineer" in msg
    assert "https://example.com/job/123" in msg


def test_format_message_includes_why_and_watch_out():
    from src.notify.telegram import format_message

    app = _make_app(
        why_match="A concrete why match reason.",
        watch_out="A specific watch out.",
    )
    msg = format_message(app)
    assert "A concrete why match reason." in msg
    assert "A specific watch out." in msg


def test_format_message_escapes_html_special_chars():
    from src.notify.telegram import format_message

    app = _make_app(
        title="Data & Analytics <Lead>",
        company="R&D > Acme",
        why_match="Uses <SQL> & Python.",
    )
    msg = format_message(app)
    assert "<Lead>" not in msg
    assert "Data &amp; Analytics &lt;Lead&gt;" in msg
    assert "R&amp;D &gt; Acme" in msg
    assert "&lt;SQL&gt;" in msg
    assert "&amp; Python" in msg


def test_format_message_handles_missing_scored_job():
    from src.notify.telegram import format_message

    app = Application(
        id=uuid4(),
        scored_job_id=uuid4(),
        status="suggested",
    )
    assert format_message(app) == ""


def test_format_message_truncates_very_long_text():
    from src.notify.telegram import format_message

    giant = "x" * 10_000
    app = _make_app(why_match=giant)
    msg = format_message(app)
    assert len(msg) <= 4096  # Telegram sendMessage cap


def test_format_message_includes_recency():
    from src.notify.telegram import format_message

    app = _make_app(age_days=0)
    msg = format_message(app)
    assert "today" in msg.lower()

    app = _make_app(age_days=5)
    msg = format_message(app)
    assert "5" in msg


# ---------------------------------------------------------------------------
# notify_all: queue empty
# ---------------------------------------------------------------------------
async def test_notify_all_empty_queue_is_noop(monkeypatch):
    from src.notify import telegram as notifier

    monkeypatch.setattr(notifier, "load_unnotified_applications", lambda limit=None: [])

    stats = await notifier.notify_all()
    assert stats["total"] == 0
    assert stats["sent"] == 0


# ---------------------------------------------------------------------------
# notify_all: DRY_RUN
# ---------------------------------------------------------------------------
async def test_notify_all_dry_run_does_not_send_or_mark(monkeypatch):
    from src.notify import telegram as notifier

    apps = [_make_app(), _make_app()]
    sent_messages: list[str] = []
    marked: list[str] = []

    async def fake_send_message(*a, **kw):
        sent_messages.append("called")
        return True

    async def fake_send_document(*a, **kw):
        sent_messages.append("called")
        return True

    monkeypatch.setattr(notifier, "load_unnotified_applications", lambda limit=None: apps)
    monkeypatch.setattr(notifier, "send_message", fake_send_message)
    monkeypatch.setattr(notifier, "send_document", fake_send_document)
    monkeypatch.setattr(notifier, "mark_application_notified", lambda aid: marked.append(aid))
    monkeypatch.setattr(notifier.settings, "dry_run", True)

    stats = await notifier.notify_all()
    assert stats["total"] == 2
    assert stats["sent"] == 0
    assert stats.get("dry_run") is True
    assert sent_messages == []
    assert marked == []


# ---------------------------------------------------------------------------
# notify_all: happy path marks each sent
# ---------------------------------------------------------------------------
async def test_notify_all_marks_each_sent_app(monkeypatch, tmp_path):
    from src.notify import telegram as notifier

    # Real files so send_document has something to pretend to open.
    resume = tmp_path / "resume.docx"
    cover = tmp_path / "cover.docx"
    resume.write_bytes(b"fake")
    cover.write_bytes(b"fake")

    apps = [
        _make_app(resume_path=str(resume), cover_letter_path=str(cover)),
        _make_app(resume_path=str(resume), cover_letter_path=str(cover)),
    ]
    marked: list[str] = []

    async def fake_send_message(*a, **kw):
        return True

    async def fake_send_document(*a, **kw):
        return True

    monkeypatch.setattr(notifier, "load_unnotified_applications", lambda limit=None: apps)
    monkeypatch.setattr(notifier, "send_message", fake_send_message)
    monkeypatch.setattr(notifier, "send_document", fake_send_document)
    monkeypatch.setattr(notifier, "mark_application_notified", lambda aid: marked.append(aid))
    monkeypatch.setattr(notifier.settings, "dry_run", False)

    stats = await notifier.notify_all()
    assert stats["total"] == 2
    assert stats["sent"] == 2
    assert stats["failed"] == 0
    assert len(marked) == 2


# ---------------------------------------------------------------------------
# notify_all: failure does NOT mark notified
# ---------------------------------------------------------------------------
async def test_notify_all_failure_does_not_mark(monkeypatch, tmp_path):
    from src.notify import telegram as notifier

    resume = tmp_path / "resume.docx"
    cover = tmp_path / "cover.docx"
    resume.write_bytes(b"x")
    cover.write_bytes(b"x")

    apps = [_make_app(resume_path=str(resume), cover_letter_path=str(cover))]
    marked: list[str] = []

    async def fake_send_message(*a, **kw):
        return False  # the message send fails

    async def fake_send_document(*a, **kw):
        return True

    monkeypatch.setattr(notifier, "load_unnotified_applications", lambda limit=None: apps)
    monkeypatch.setattr(notifier, "send_message", fake_send_message)
    monkeypatch.setattr(notifier, "send_document", fake_send_document)
    monkeypatch.setattr(notifier, "mark_application_notified", lambda aid: marked.append(aid))
    monkeypatch.setattr(notifier.settings, "dry_run", False)

    stats = await notifier.notify_all()
    assert stats["total"] == 1
    assert stats["sent"] == 0
    assert stats["failed"] == 1
    assert marked == []


# ---------------------------------------------------------------------------
# notify_all: respects MAX_JOBS_PER_RUN cap
# ---------------------------------------------------------------------------
async def test_notify_all_respects_cap(monkeypatch):
    from src.notify import telegram as notifier

    captured_limit: list[int | None] = []

    def fake_load(limit=None):
        captured_limit.append(limit)
        return []

    monkeypatch.setattr(notifier, "load_unnotified_applications", fake_load)
    monkeypatch.setattr(notifier.settings, "max_jobs_per_run", 3)

    await notifier.notify_all()
    assert captured_limit == [3]
