from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from probity.config import get_settings
from probity.db.models import Base, TelemetryBase


@lru_cache
def get_engine() -> Engine:
    return _make_engine(get_settings().database_url)


@lru_cache
def get_telemetry_engine() -> Engine:
    url = get_settings().database_url
    if url.startswith("sqlite:///") and not url.endswith(":memory:"):
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
    return create_engine(url, pool_pre_ping=True)


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


@lru_cache
def get_telemetry_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_telemetry_engine(), expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(get_engine())
    TelemetryBase.metadata.create_all(get_telemetry_engine())


def drop_all() -> None:
    Base.metadata.drop_all(get_engine())
    TelemetryBase.metadata.drop_all(get_telemetry_engine())


@contextmanager
def session_scope() -> Iterator[Session]:
    s = get_sessionmaker()()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


@contextmanager
def telemetry_scope() -> Iterator[Session]:
    s = get_telemetry_sessionmaker()()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
