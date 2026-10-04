"""Shared Redis access (optional). Everything degrades gracefully when REDIS_URL is unset."""

from __future__ import annotations

from functools import lru_cache

from probity.config import get_settings


@lru_cache
def sync_redis():  # type: ignore[no-untyped-def]
    url = get_settings().redis_url
    if not url:
        return None
    import redis

    return redis.Redis.from_url(url, socket_timeout=2, socket_connect_timeout=2, health_check_interval=30)


def async_redis():  # type: ignore[no-untyped-def]
    url = get_settings().redis_url
    if not url:
        return None
    import redis.asyncio as aioredis

    return aioredis.Redis.from_url(url)


def channel(workspace_id: str, case_id: str) -> str:
    return f"probity:events:{workspace_id}:{case_id}"
