"""Request-body size limits, enforced before any route, form parser or auth dependency runs (Security.md §6).

FastAPI parses multipart forms before resolving dependencies, so without this an unauthenticated client could make
the server spool arbitrarily large uploads to disk. Declared sizes over the limit are refused immediately; streamed
(chunked) bodies are counted as they arrive and cut off at the limit.
"""

from __future__ import annotations

import json
from typing import Any

MB = 1024 * 1024
DEFAULT_LIMIT = 1 * MB
# Most specific prefix first. Upload limit = 15 MB document + multipart overhead; imports = 5 MB CSV + overhead.
ROUTE_LIMITS: list[tuple[str, str, int]] = [
    ("POST", "/api/v1/documents", 16 * MB),
    ("POST", "/api/v1/imports/", 6 * MB),
]


class _TooLarge(Exception):
    pass


def limit_for(method: str, path: str) -> int:
    for m, prefix, limit in ROUTE_LIMITS:
        if method == m and (path == prefix or path.startswith(prefix)):
            return limit
    return DEFAULT_LIMIT


async def _reject(send: Any, limit: int) -> None:
    body = json.dumps({"error": {"code": "payload_too_large", "message": f"request body exceeds {limit // MB or 1} MB", "details": {}}}).encode()
    await send({"type": "http.response.start", "status": 413,
                "headers": [(b"content-type", b"application/json; charset=utf-8"), (b"content-length", str(len(body)).encode()),
                            (b"x-content-type-options", b"nosniff")]})
    await send({"type": "http.response.body", "body": body})


class BodySizeLimit:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = limit_for(scope.get("method", ""), scope.get("path", ""))
        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None:
            try:
                if int(declared) > limit:
                    await _reject(send, limit)
                    return
            except ValueError:
                await _reject(send, limit)
                return
        received = 0
        started = False

        async def counted_receive() -> dict:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _TooLarge()
            return message

        async def tracked_send(message: dict) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counted_receive, tracked_send)
        except _TooLarge:
            if not started:
                await _reject(send, limit)
