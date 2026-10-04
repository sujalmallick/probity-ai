"""Structured logging (JSON in production) with PII scrubbing (Security.md §4, Guardrails G8).

Never log full documents, tokens, or prompts; traces reference IDs. The scrubber masks account numbers,
PAN, emails, bearer tokens and secrets that slip into log fields anyway.
"""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

import structlog

from probity.config import get_settings

_SCRUB = [
    (re.compile(r"(?i)bearer\s+[a-z0-9._\-]+"), "Bearer <redacted>"),
    (re.compile(r"\b\d{9,18}\b"), "<ACCT>"),
    (re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b"), "<PAN>"),
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "<EMAIL>"),
    (re.compile(r"(?i)(sk-ant-|re_|sk_live_|sk_test_)[a-z0-9_\-]{8,}"), "<SECRET>"),
]
_SENSITIVE_KEYS = {"password", "token", "authorization", "secret", "api_key", "body", "excerpt", "text", "prompt"}


def scrub(value: Any) -> Any:
    if isinstance(value, str):
        for pat, repl in _SCRUB:
            value = pat.sub(repl, value)
        return value
    if isinstance(value, dict):
        return {k: ("<redacted>" if k.lower() in _SENSITIVE_KEYS else scrub(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(v) for v in value]
    return value


def _scrub_processor(_logger: Any, _name: str, event_dict: dict) -> dict:
    return {k: (v if k in ("timestamp", "level", "logger") else scrub(v)) for k, v in event_dict.items()}


_configured = False


def configure_logging() -> None:
    global _configured
    if _configured:
        return
    s = get_settings()
    level = getattr(logging, s.log_level.upper(), logging.INFO)
    renderer = structlog.processors.JSONRenderer() if s.log_json else structlog.dev.ConsoleRenderer(colors=False)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _scrub_processor,
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(level=level, format="%(message)s", stream=sys.stdout)
    if s.sentry_dsn:
        import sentry_sdk

        sentry_sdk.init(dsn=s.sentry_dsn, environment=s.env, traces_sample_rate=0.05, send_default_pii=False,
                        before_send=lambda event, hint: scrub(event))
    _configured = True


def get_logger(name: str) -> Any:
    return structlog.get_logger(name)
