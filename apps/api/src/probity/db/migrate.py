"""Schema management for PostgreSQL (Alembic).   python -m probity.db.migrate upgrade|reset|revision -m "msg"

SQLite dev/test databases are created with metadata.create_all instead.
"""

from __future__ import annotations

import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from probity.config import get_settings

MIGRATIONS = Path(__file__).resolve().parent / "migrations"


def migrate_url() -> str:
    s = get_settings()
    return s.database_migrate_url or s.database_url


def alembic_config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.set_main_option("sqlalchemy.url", migrate_url().replace("%", "%%"))
    return cfg


def upgrade(rev: str = "head") -> None:
    command.upgrade(alembic_config(), rev)


def reset_postgres() -> None:
    """Drop everything and re-apply migrations. Refuses to run in prod."""
    if get_settings().env == "prod":
        raise RuntimeError("refusing to reset a production database")
    from sqlalchemy import create_engine

    with create_engine(migrate_url()).begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    upgrade()


def main() -> None:
    args = sys.argv[1:] or ["upgrade"]
    if args[0] == "upgrade":
        upgrade(args[1] if len(args) > 1 else "head")
        print("database at head")
    elif args[0] == "reset":
        reset_postgres()
        print("database reset to head")
    elif args[0] == "revision":
        msg = args[args.index("-m") + 1] if "-m" in args else "change"
        command.revision(alembic_config(), message=msg, autogenerate=True)
    else:
        raise SystemExit(f"unknown command {args[0]}")


if __name__ == "__main__":
    main()
