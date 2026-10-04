"""Auth, RBAC, rate limiting. workspace_id always comes from the token, never the request body."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, Header, HTTPException, Query, Request
from sqlalchemy.orm import Session

from probity.config import get_settings
from probity.db.models import User
from probity.db.session import get_sessionmaker, set_tenant


def db() -> Iterator[Session]:
    s = get_sessionmaker()()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def issue_token(user: User) -> str:
    st = get_settings()
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"sub": user.id, "ws": user.workspace_id, "role": user.role, "iat": now, "exp": now + timedelta(minutes=st.jwt_ttl_minutes)},
        st.jwt_secret,
        algorithm="HS256",
    )


def _decode(token: str) -> dict:
    st = get_settings()
    if st.auth_mode != "local":
        raise HTTPException(501, "Clerk/Supabase JWKS verification not configured in this build")
    try:
        return jwt.decode(token, st.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as e:
        raise HTTPException(401, "invalid or expired token") from e


def current_user(
    s: Session = Depends(db),
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None, description="SSE only: EventSource cannot set headers"),
) -> User:
    raw = authorization.split(" ", 1)[1] if authorization and authorization.lower().startswith("bearer ") else token
    if not raw:
        raise HTTPException(401, "missing bearer token")
    claims = _decode(raw)
    user = s.get(User, claims["sub"])
    if user is None or user.workspace_id != claims["ws"] or not user.active:
        raise HTTPException(401, "unknown user")
    set_tenant(s, user.workspace_id)
    _rate_limit(f"u:{user.id}", 120, 60)
    return user


_buckets: dict[str, deque[float]] = defaultdict(deque)
_rl_lock = threading.Lock()


def _rate_limit(key: str, limit: int, window: int) -> None:
    now = time.monotonic()
    with _rl_lock:
        q = _buckets[key]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            raise HTTPException(429, "rate limit exceeded")
        q.append(now)


def upload_limit(request: Request, user: User = Depends(current_user)) -> User:
    _rate_limit(f"up:{user.id}", 10, 60)
    return user
