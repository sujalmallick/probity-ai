"""Least-privilege grants for the runtime role (if present).

The API/worker should connect as a role that is neither superuser nor table owner, so row-level
security is enforced. If a `probity_app` role exists (local Docker, self-hosted), grant it exactly
what it needs; on managed Postgres where the app user is the (non-superuser) owner, FORCE RLS covers it.

Revision ID: 0003
Revises: 0002
"""
from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'probity_app') THEN
                GRANT USAGE ON SCHEMA public TO probity_app;
                GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO probity_app;
                GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO probity_app;
                REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM probity_app;
                REVOKE UPDATE, DELETE, TRUNCATE ON evidence FROM probity_app;
                REVOKE ALL ON alembic_version FROM probity_app;
                GRANT EXECUTE ON FUNCTION probity_case_workspace(text) TO probity_app;
            END IF;
        END $$;
    """)


def downgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'probity_app') THEN
                REVOKE ALL ON ALL TABLES IN SCHEMA public FROM probity_app;
                REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM probity_app;
            END IF;
        END $$;
    """)
