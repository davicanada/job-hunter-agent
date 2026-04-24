"""Settings loaded from environment variables and validated at import time."""
from __future__ import annotations

import os

from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError

load_dotenv()


class Settings(BaseModel):
    supabase_url: str = Field(min_length=1)
    supabase_service_key: str = Field(min_length=1)
    supabase_database_password: str = ""

    groq_api_key: str = Field(min_length=1)
    groq_model: str = "llama-3.3-70b-versatile"

    telegram_bot_token: str = Field(min_length=1)
    telegram_chat_id: str = Field(min_length=1)

    min_score_to_notify: int = 70
    max_jobs_per_run: int = 5
    dry_run: bool = False
    log_level: str = "INFO"

    gmail_client_id: str = ""
    gmail_client_secret: str = ""
    gmail_refresh_token: str = ""

    github_actions: bool = False


_REQUIRED = (
    "SUPABASE_URL",
    "SUPABASE_SERVICE_KEY",
    "GROQ_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
)


def _bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _load() -> Settings:
    missing = [n for n in _REQUIRED if not os.environ.get(n, "").strip()]
    if missing:
        raise RuntimeError(
            "Missing required environment variables: "
            + ", ".join(missing)
            + ". Copy .env.example to .env and fill them in."
        )

    try:
        return Settings(
            supabase_url=os.environ["SUPABASE_URL"].strip(),
            supabase_service_key=os.environ["SUPABASE_SERVICE_KEY"].strip(),
            supabase_database_password=os.environ.get("SUPABASE_DATABASE_PASSWORD", "").strip(),
            groq_api_key=os.environ["GROQ_API_KEY"].strip(),
            groq_model=os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile").strip(),
            telegram_bot_token=os.environ["TELEGRAM_BOT_TOKEN"].strip(),
            telegram_chat_id=os.environ["TELEGRAM_CHAT_ID"].strip(),
            min_score_to_notify=int(os.environ.get("MIN_SCORE_TO_NOTIFY", "70")),
            max_jobs_per_run=int(os.environ.get("MAX_JOBS_PER_RUN", "5")),
            dry_run=_bool("DRY_RUN", False),
            log_level=os.environ.get("LOG_LEVEL", "INFO").strip().upper(),
            gmail_client_id=os.environ.get("GMAIL_CLIENT_ID", ""),
            gmail_client_secret=os.environ.get("GMAIL_CLIENT_SECRET", ""),
            gmail_refresh_token=os.environ.get("GMAIL_REFRESH_TOKEN", ""),
            github_actions=_bool("GITHUB_ACTIONS", False),
        )
    except ValidationError as e:
        raise RuntimeError(f"Invalid settings: {e}") from e


settings = _load()
