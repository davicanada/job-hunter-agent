"""Telegram Bot notifier — sends one message + .docx attachments per match.

Flow:
1. ``load_unnotified_applications`` returns every ``applications`` row with
   ``status='suggested'`` AND ``notified_at IS NULL`` (eagerly joined with
   the scored_job + job rows).
2. For each, ``notify_one`` sends an HTML summary via ``sendMessage`` then
   posts the resume and cover letter via ``sendDocument``.
3. On success, ``mark_application_notified`` stamps ``notified_at = NOW()``
   so the same row is never re-sent on a later cron run.

``DRY_RUN=true`` short-circuits the HTTP calls so you can see what would
have been sent without actually publishing. ``MAX_JOBS_PER_RUN`` caps how
many messages go out per invocation (default 5).
"""
from __future__ import annotations

import html
from pathlib import Path

import httpx

from config.settings import settings
from src.db.client import load_unnotified_applications, mark_application_notified
from src.models.job import Application
from src.utils.logger import get_logger

log = get_logger(__name__)

TELEGRAM_API = "https://api.telegram.org"
SEND_TIMEOUT_S = 30.0
MAX_MESSAGE_CHARS = 4096  # Telegram sendMessage limit
_DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


def format_message(app: Application) -> str:
    """Return HTML-formatted Telegram message for one application.

    Returns an empty string if the application has no attached ``scored_job``
    or ``job`` — callers treat that as a skip.
    """
    sj = app.scored_job
    if sj is None or sj.job is None:
        return ""

    title = html.escape(sj.job.title or "")
    company = html.escape(sj.job.company or "")
    verdict = sj.verdict or ""
    track = sj.track or "other"

    if sj.age_days is None:
        recency_note = ""
    elif sj.age_days <= 0:
        recency_note = " • Posted today"
    else:
        recency_note = f" • Posted {sj.age_days}d ago"

    header = (
        f"🎯 <b>{title}</b> at <b>{company}</b>\n"
        f"Score: {sj.score} ({verdict}) • Track: {track}{recency_note}"
    )

    sections: list[str] = [header]
    if sj.why_match:
        sections.append(f"✓ <i>Why match:</i> {html.escape(sj.why_match)}")
    if sj.watch_out:
        sections.append(f"⚠️ <i>Watch out:</i> {html.escape(sj.watch_out)}")
    if sj.job.url:
        sections.append(f'🔗 <a href="{html.escape(sj.job.url)}">View posting</a>')

    message = "\n\n".join(sections)
    if len(message) > MAX_MESSAGE_CHARS:
        message = message[: MAX_MESSAGE_CHARS - 1] + "…"
    return message


async def send_message(bot_token: str, chat_id: str, text: str) -> bool:
    """POST to Telegram ``sendMessage``. Returns True on HTTP 200."""
    url = f"{TELEGRAM_API}/bot{bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=SEND_TIMEOUT_S) as client:
            resp = await client.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": False,
                },
            )
    except Exception as e:  # noqa: BLE001
        log.error("notify.telegram.send_message.exception", error=str(e))
        return False
    if resp.status_code != 200:
        log.error(
            "notify.telegram.send_message.http_error",
            status=resp.status_code,
            body=resp.text[:300],
        )
        return False
    return True


async def send_document(bot_token: str, chat_id: str, path: Path) -> bool:
    """POST to Telegram ``sendDocument`` as multipart upload."""
    url = f"{TELEGRAM_API}/bot{bot_token}/sendDocument"
    try:
        with path.open("rb") as fh:
            files = {"document": (path.name, fh, _DOCX_MIME)}
            data = {"chat_id": chat_id}
            async with httpx.AsyncClient(timeout=SEND_TIMEOUT_S) as client:
                resp = await client.post(url, data=data, files=files)
    except FileNotFoundError:
        log.error("notify.telegram.send_document.missing_file", path=str(path))
        return False
    except Exception as e:  # noqa: BLE001
        log.error(
            "notify.telegram.send_document.exception",
            path=str(path),
            error=str(e),
        )
        return False
    if resp.status_code != 200:
        log.error(
            "notify.telegram.send_document.http_error",
            status=resp.status_code,
            path=str(path),
            body=resp.text[:300],
        )
        return False
    return True


async def notify_one(app: Application) -> bool:
    """Send summary + attachments for one application. Return True when the
    summary message lands; document failures are logged but don't fail the
    whole app (the message is the primary deliverable)."""
    text = format_message(app)
    if not text:
        log.warning("notify.telegram.empty_message", app_id=str(app.id))
        return False

    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id

    if not await send_message(token, chat_id, text):
        return False

    for path_str in (app.resume_path, app.cover_letter_path):
        if not path_str:
            continue
        path = Path(path_str)
        if not path.exists():
            log.warning(
                "notify.telegram.missing_file",
                path=path_str,
                app_id=str(app.id),
            )
            continue
        if not await send_document(token, chat_id, path):
            log.error(
                "notify.telegram.attachment_failed",
                path=path_str,
                app_id=str(app.id),
            )
    return True


async def notify_all() -> dict:
    """Send every unnotified application, capped at ``MAX_JOBS_PER_RUN``."""
    limit = settings.max_jobs_per_run
    apps = load_unnotified_applications(limit=limit)
    if not apps:
        log.info("notify.telegram.queue_empty")
        return {"total": 0, "sent": 0, "failed": 0}

    log.info("notify.telegram.started", total=len(apps))

    if settings.dry_run:
        for app in apps:
            sj = app.scored_job
            log.info(
                "notify.telegram.dry_run_would_send",
                app_id=str(app.id),
                title=(sj.job.title if sj and sj.job else None),
                score=(sj.score if sj else None),
                verdict=(sj.verdict if sj else None),
            )
        return {
            "total": len(apps),
            "sent": 0,
            "failed": 0,
            "dry_run": True,
        }

    sent = 0
    failed = 0
    for app in apps:
        try:
            ok = await notify_one(app)
        except Exception as e:  # noqa: BLE001
            log.error(
                "notify.telegram.send_crashed",
                app_id=str(app.id),
                error=str(e),
            )
            failed += 1
            continue
        if ok:
            try:
                mark_application_notified(str(app.id))
            except Exception as e:  # noqa: BLE001
                # The message was delivered but we couldn't stamp the DB.
                # Surface the error loudly — a re-run would double-send.
                log.error(
                    "notify.telegram.mark_notified_failed",
                    app_id=str(app.id),
                    error=str(e),
                )
            sent += 1
        else:
            failed += 1

    log.info(
        "notify.telegram.finished",
        total=len(apps),
        sent=sent,
        failed=failed,
    )
    return {"total": len(apps), "sent": sent, "failed": failed}
