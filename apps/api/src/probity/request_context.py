"""Per-request context readable anywhere in the call stack (set by the API middleware)."""

from __future__ import annotations

import contextvars

request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("probity_request_id", default=None)
