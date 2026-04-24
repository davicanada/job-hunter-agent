"""Pytest config. Sets dummy env vars before any test module imports.

The smoke suite does not make network calls, but ``config.settings`` validates
required env vars at import time, and ``setdefault`` lets a real ``.env`` from
the developer's shell still win if one is already loaded.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Make the project root importable as-is (so `from src.models.job import Job`
# works without a src-layout install).
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")
os.environ.setdefault("GROQ_API_KEY", "test-groq-key")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-telegram-token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "0")
