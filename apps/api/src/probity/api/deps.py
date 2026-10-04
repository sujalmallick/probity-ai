"""Auth, RBAC, rate limiting. workspace_id always comes from the token, never the request body."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from collections.abc import Iterator

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from probity.auth import AuthError, Principal, authenticate, issue_local_token
from probity.config import get_settings
from probity.db.models import User, Workspace
from probity.db.session import get_sessionmaker
from probity.redis_client import sync_redis


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


issue_token = issue_local_token


def principal(request: Request, s: Session = Depends(db), authorization: str | None = Header(default=None)) -> Principal:
    """Bearer token from the Authorization header only (never from the URL)."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    try:
        p = authenticate(authorization.split(" ", 1)[1].strip(), s)
    except AuthError as e:
        raise HTTPException(401, str(e)) from e
    request.state.principal = p
    request.state.user_id, request.state.workspace_id = p.user.id, p.user.workspace_id
    _rate_limit(f"u:{p.user.id}", 120, 60)
    return p


def current_user(p: Principal = Depends(principal)) -> User:
    return p.user


def require_mfa_for_approvals(p: Principal = Depends(principal), s: Session = Depends(db)) -> User:
    """Approver actions (decisions, sends, out-of-band confirmations) require a second factor when the
    workspace policy says so (Security.md §2). Enforced only with Clerk, which reports factor verification."""
    from probity.db.models import Workspace
    from probity.policy import get_policy

    if get_policy(s.get(Workspace, p.user.workspace_id)).get("require_mfa_for_approvals") and not p.mfa_verified:
        raise HTTPException(403, "this action requires multi-factor authentication — enable MFA in your account and sign in again")
    return p.user


_buckets: dict[str, deque[float]] = defaultdict(deque)
_rl_lock = threading.Lock()


def _rate_limit(key: str, limit: int, window: int) -> None:
    """Fixed-window limit in Redis (shared by all API instances); in-process fallback without Redis."""
    r = sync_redis()
    if r is not None:
        try:
            bucket = f"probity:rl:{key}:{int(time.time()) // window}"
            n = r.incr(bucket)
            if n == 1:
                r.expire(bucket, window + 1)
            if n > limit:
                raise HTTPException(429, "rate limit exceeded")
            return
        except HTTPException:
            raise
        except Exception:  # noqa: BLE001 - Redis down: fall back to local limiting rather than fail open entirely
            pass
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


# ---------------------------------------------------------------- demo sign-in (AUTH_MODE=local only)

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def demo_on() -> bool:
    st = get_settings()
    return st.demo_features and st.env != "prod"


def demo_login_on(request: Request) -> bool:
    """Passwordless "sign in as anyone" is a local-development convenience, so it is never reachable by default:
    ENV=dev serves it to this machine only; ENV=demo (an explicit public demo, which requires a real JWT_SECRET)
    serves it for the seeded demo workspace only; ENV=test for the test suite; ENV=prod never."""
    st = get_settings()
    if st.auth_mode != "local" or not demo_on():
        return False
    if st.env == "dev":
        return (request.client.host if request.client else "") in _LOOPBACK
    return st.env in ("demo", "test")


def demo_login_users(s: Session) -> list[User]:
    """In a public demo only the seeded demo workspace can be entered; every other workspace needs real sign-in."""
    q = select(User).where(User.active.is_(True)).order_by(User.role)
    if get_settings().env == "demo":
        from probity.demo.seed import DEMO_WORKSPACE_NAME

        q = q.join(Workspace, Workspace.id == User.workspace_id).where(Workspace.name == DEMO_WORKSPACE_NAME)
    return list(s.scalars(q))
