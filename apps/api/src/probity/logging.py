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
from probity.ingestion.validators import EMAIL_RE

_SCRUB = [
    (re.compile(r"(?i)bearer\s+[a-z0-9._\-]+"), "Bearer <redacted>"),
    (re.compile(r"\b\d{9,18}\b"), "<ACCT>"),
    (re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b"), "<PAN>"),
    (EMAIL_RE, "<EMAIL>"),
    (re.compile(r"(?i)(sk-ant-|re_|sk_live_|sk_test_|tvly-)[a-z0-9_\-]{8,}"), "<SECRET>"),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "<SECRET>"),
    (re.compile(r"(://[^:/@\s]*:)[^@\s]*@"), r"\1***@"),  # password inside a connection URL
]
_SENSITIVE_KEYS = {"body", "excerpt", "text", "prompt"}
_SENSITIVE_KEY_PARTS = ("password", "passwd", "secret", "token", "authorization", "api_key", "apikey", "dsn", "cookie", "private_key", "field_key", "hmac_key")


def _sensitive(key: str) -> bool:
    k = key.lower()
    return k in _SENSITIVE_KEYS or any(part in k for part in _SENSITIVE_KEY_PARTS)


def scrub(value: Any) -> Any:
    if isinstance(value, str):
        for pat, repl in _SCRUB:
            value = pat.sub(repl, value)
        return value
    if isinstance(value, dict):
        return {k: ("<redacted>" if isinstance(k, str) and _sensitive(k) else scrub(v)) for k, v in value.items()}
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
            structlog.processors.format_exc_info,  # before scrubbing, so traceback text is scrubbed too
            _scrub_processor,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(level=level, format="%(message)s", stream=sys.stdout)
    if s.sentry_dsn:
        import sentry_sdk

        sentry_sdk.init(dsn=s.sentry_dsn, **sentry_options(s.env))
    _configured = True


def sentry_options(env: str) -> dict[str, Any]:
    """No frame locals (they hold settings, tokens and invoice text), no request bodies, no PII; scrub what remains."""
    return {
        "environment": env, "traces_sample_rate": 0.05, "send_default_pii": False, "include_local_variables": False,
        "max_request_body_size": "never", "before_send": lambda event, hint: scrub(event),
        "before_breadcrumb": lambda crumb, hint: scrub(crumb),
    }


def get_logger(name: str) -> Any:
    return structlog.get_logger(name)
