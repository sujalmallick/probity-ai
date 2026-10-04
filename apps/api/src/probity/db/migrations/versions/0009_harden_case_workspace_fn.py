"""Harden probity_case_workspace(): pinned search_path, schema-qualified table, EXECUTE only for the app role

The function is SECURITY DEFINER (it runs as its owner so the inbound-email webhook can find a case's workspace
before any tenant is bound). With `search_path = public` and an unqualified table name, a role able to create
objects earlier on the path (e.g. in pg_temp) could shadow `cases`; and EXECUTE was granted to PUBLIC, so any
database role could map arbitrary case ids to workspaces.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-04 23:30:00
"""
from __future__ import annotations

from alembic import op

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION probity_case_workspace(p_case_id text) RETURNS text
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
            SELECT workspace_id FROM public.cases WHERE id = p_case_id
        $$;
    """)
    op.execute("REVOKE ALL ON FUNCTION probity_case_workspace(text) FROM PUBLIC")
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'probity_app') THEN
                GRANT EXECUTE ON FUNCTION probity_case_workspace(text) TO probity_app;
            END IF;
        END $$;
    """)


def downgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION probity_case_workspace(p_case_id text) RETURNS text
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
            SELECT workspace_id FROM cases WHERE id = p_case_id
        $$;
    """)
    op.execute("GRANT EXECUTE ON FUNCTION probity_case_workspace(text) TO PUBLIC")
