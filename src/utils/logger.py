"""Structured logging. JSON output in CI, pretty console locally."""
from __future__ import annotations

import logging
import os
import sys
from typing import Any

import structlog

_configured = False


def _in_ci() -> bool:
    return os.environ.get("GITHUB_ACTIONS", "").strip().lower() in {"1", "true"}


def _configure() -> None:
    global _configured
    if _configured:
        return

    # Windows consoles default to cp1252 and crash on non-ASCII job titles
    # (e.g. "Développeur Données"). Force UTF-8 with a permissive error
    # handler so logging never kills the pipeline.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="backslashreplace")
            except (ValueError, OSError):
                pass

    level_name = os.environ.get("LOG_LEVEL", "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)

    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if _in_ci():
        renderer: Any = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())

    structlog.configure(
        processors=[*shared, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)

    _configured = True


def get_logger(name: str) -> Any:
    _configure()
    return structlog.get_logger(name)
