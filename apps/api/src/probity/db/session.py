"""Engines, sessions and tenant context.

PostgreSQL only; the schema is created and upgraded by Alembic (`python -m probity.bootstrap`).

Tenant isolation (Security.md §3): every transaction runs
`set_config('app.workspace_id', <ws>, true)` and row-level-security policies filter every tenant table
on it. The setting is transaction-local, so a pooled connection can never leak one tenant's context
into the next request. The workspace comes from (in order) the session's `info["workspace_id"]` or the
`tenant()` context variable; with neither set, RLS returns no rows (fail closed).
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from probity.config import ConfigError, get_settings

_tenant: contextvars.ContextVar[str | None] = contextvars.ContextVar("probity_tenant", default=None)


@contextmanager
def tenant(workspace_id: str | None) -> Iterator[None]:
    """Run a block on behalf of one workspace (worker tasks, graph nodes, services)."""
    token = _tenant.set(workspace_id)
    try:
        yield
    finally:
        _tenant.reset(token)


def current_tenant() -> str | None:
    return _tenant.get()


@lru_cache
def get_engine() -> Engine:
    st = get_settings()
    if not st.database_url or not st.database_url.startswith("postgresql"):
        raise ConfigError("DATABASE_URL must be set to a PostgreSQL URL")
    return create_engine(st.database_url, pool_pre_ping=True, pool_size=st.db_pool_size, max_overflow=st.db_pool_overflow, pool_recycle=1800)


def _apply_tenant(session: Session, connection) -> None:  # type: ignore[no-untyped-def]
    ws = session.info.get("workspace_id") or _tenant.get() or ""
    connection.execute(text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": ws})


def _make_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    sm = sessionmaker(bind=engine, expire_on_commit=False)

    @event.listens_for(sm, "after_begin")
    def _after_begin(session, transaction, connection):  # type: ignore[no-untyped-def]
        _apply_tenant(session, connection)

    return sm


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return _make_sessionmaker(get_engine())


@lru_cache
def get_telemetry_sessionmaker() -> sessionmaker[Session]:
    """Separate sessions (same database) so progress events and LLM call records commit independently of the
    case transaction that is running."""
    return _make_sessionmaker(get_engine())


def set_tenant(s: Session, workspace_id: str) -> None:
    """Bind a session to a workspace, including the transaction already in progress."""
    s.info["workspace_id"] = workspace_id
    if s.in_transaction():
        s.execute(text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": workspace_id})


@contextmanager
def session_scope(workspace_id: str | None = None) -> Iterator[Session]:
    s = get_sessionmaker()()
    if workspace_id or _tenant.get():
        s.info["workspace_id"] = workspace_id or _tenant.get()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


@contextmanager
def telemetry_scope(workspace_id: str | None = None) -> Iterator[Session]:
    s = get_telemetry_sessionmaker()()
    if workspace_id or _tenant.get():
        s.info["workspace_id"] = workspace_id or _tenant.get()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
