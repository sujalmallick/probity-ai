"""Engines, sessions and tenant context.

Tenant isolation (Security.md §3): on Postgres every transaction runs
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

from probity.config import get_settings
from probity.db.models import Base, TelemetryBase

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


def is_postgres() -> bool:
    return get_settings().database_url.startswith("postgresql")


@lru_cache
def get_engine() -> Engine:
    return _make_engine(get_settings().database_url)


@lru_cache
def get_telemetry_engine() -> Engine:
    url = get_settings().database_url
    if url.startswith("sqlite:///") and not url.endswith(":memory:"):
        # SQLite has one writer; keep live progress events off the case-write lock.
        return _make_engine(url.removesuffix(".db") + ".telemetry.db")
    return get_engine()


def _make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        from pathlib import Path

        path = url.split("///", 1)[-1]
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _):  # type: ignore[no-untyped-def]
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        return engine
    s = get_settings()
    return create_engine(url, pool_pre_ping=True, pool_size=s.db_pool_size, max_overflow=s.db_pool_overflow, pool_recycle=1800)


def _apply_tenant(session: Session, connection) -> None:  # type: ignore[no-untyped-def]
    if connection.dialect.name != "postgresql":
        return
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
    return _make_sessionmaker(get_telemetry_engine())


def set_tenant(s: Session, workspace_id: str) -> None:
    """Bind a session to a workspace, including the transaction already in progress."""
    s.info["workspace_id"] = workspace_id
    if s.in_transaction() and s.get_bind().dialect.name == "postgresql":
        s.execute(text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": workspace_id})


def init_db() -> None:
    """Dev/test only. On Postgres the schema is owned by Alembic (`probity db upgrade`)."""
    if is_postgres():
        return
    Base.metadata.create_all(get_engine())
    TelemetryBase.metadata.create_all(get_telemetry_engine())


def drop_all() -> None:
    if is_postgres():
        from probity.db.migrate import reset_postgres

        reset_postgres()
        return
    Base.metadata.drop_all(get_engine())
    TelemetryBase.metadata.drop_all(get_telemetry_engine())


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
