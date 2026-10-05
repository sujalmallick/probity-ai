"""Account-level AI problems (credits or quota used up, key rejected, AI not configured), remembered so the web app
can tell people why summaries and checks are falling back to rules — instead of only a small badge on each result.

Only problems that affect *every* AI call are recorded; per-request issues (one invalid output, a timeout) are not.
The state clears on the next successful AI call. Stored in Redis when configured (shared by API and workers),
otherwise in this process.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any

ACCOUNT_CODES = frozenset({"credits_exhausted", "quota_exhausted", "key_invalid", "not_configured"})
_KEY = "probity:ai_health"
_TTL_SECONDS = 24 * 3600
_lock = threading.Lock()
_local: dict[str, Any] | None = None


def _redis():  # type: ignore[no-untyped-def]
    try:
        from probity.redis_client import sync_redis

        return sync_redis()
    except Exception:  # noqa: BLE001 - health tracking must never break an AI call
        return None


def record_failure(code: str, reason: str) -> None:
    if code not in ACCOUNT_CODES:
        return
    global _local
    state = {"code": code, "reason": reason, "since": datetime.now(timezone.utc).isoformat()}
    r = _redis()
    if r is not None:
        try:
            r.set(_KEY, json.dumps(state), ex=_TTL_SECONDS)
            return
        except Exception:  # noqa: BLE001
            pass
    with _lock:
        if _local is None or _local.get("code") != code:
            _local = state


def record_success() -> None:
    global _local
    r = _redis()
    if r is not None:
        try:
            r.delete(_KEY)
        except Exception:  # noqa: BLE001
            pass
    with _lock:
        _local = None


def current() -> dict[str, Any] | None:
    """The active account-level AI problem ({code, reason, since}) or None when the AI is working."""
    r = _redis()
    if r is not None:
        try:
            raw = r.get(_KEY)
            return json.loads(raw) if raw else None
        except Exception:  # noqa: BLE001
            pass
    with _lock:
        return dict(_local) if _local else None
