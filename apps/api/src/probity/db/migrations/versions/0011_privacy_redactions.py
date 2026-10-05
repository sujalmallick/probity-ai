"""privacy redactions: erase a vendor contact's details from the audit log and evidence without breaking the chain

The audit log and evidence stay immutable for the app role (no UPDATE grant, and a trigger). The only way to change
them is through two SECURITY DEFINER functions that replace content in one row of the caller's workspace, and only
after a privacy_redactions row (carrying an HMAC over the new content) exists for it. verify_chain checks that HMAC
for redacted rows instead of recomputing the original hash.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-05 12:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'privacy_redactions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('workspace_id', sa.String(length=40), nullable=False),
        sa.Column('table_name', sa.String(length=30), nullable=False),
        sa.Column('row_id', sa.String(length=40), nullable=False),
        sa.Column('erasure_id', sa.String(length=40), nullable=False),
        sa.Column('erased_by', sa.String(length=40), nullable=False),
        sa.Column('erased_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('mac', sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('table_name', 'row_id'),
    )
    op.create_index(op.f('ix_privacy_redactions_workspace_id'), 'privacy_redactions', ['workspace_id'], unique=False)
    op.execute("ALTER TABLE privacy_redactions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE privacy_redactions FORCE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY tenant_isolation ON privacy_redactions USING (workspace_id = current_setting('app.workspace_id', true)) "
               "WITH CHECK (workspace_id = current_setting('app.workspace_id', true))")

    # The immutability trigger lets an UPDATE through only while one of the functions below has switched it on for
    # its own statement. The app role has no UPDATE privilege on either table, so it can't use the switch directly.
    op.execute("""
        CREATE OR REPLACE FUNCTION probity_forbid_change() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'UPDATE' AND current_setting('probity.redacting', true) = 'on' THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION '% is immutable (% blocked)', TG_TABLE_NAME, TG_OP;
        END $$;
    """)
    # Row-level security still applies inside (FORCE), and the explicit workspace filter says so twice.
    op.execute("""
        CREATE FUNCTION probity_redact_audit(p_id integer, p_data jsonb) RETURNS void
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM public.privacy_redactions WHERE table_name = 'audit_log' AND row_id = p_id::text
                           AND workspace_id = current_setting('app.workspace_id', true)) THEN
                RAISE EXCEPTION 'audit_log row % has no redaction record', p_id;
            END IF;
            PERFORM set_config('probity.redacting', 'on', true);
            UPDATE public.audit_log SET data = p_data WHERE id = p_id AND workspace_id = current_setting('app.workspace_id', true);
            PERFORM set_config('probity.redacting', 'off', true);
        END $$;
    """)
    op.execute("""
        CREATE FUNCTION probity_redact_evidence(p_id text, p_source_ref text, p_excerpt text, p_value jsonb) RETURNS void
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM public.privacy_redactions WHERE table_name = 'evidence' AND row_id = p_id
                           AND workspace_id = current_setting('app.workspace_id', true)) THEN
                RAISE EXCEPTION 'evidence row % has no redaction record', p_id;
            END IF;
            PERFORM set_config('probity.redacting', 'on', true);
            UPDATE public.evidence SET source_ref = p_source_ref, excerpt = p_excerpt, value = p_value
             WHERE id = p_id AND workspace_id = current_setting('app.workspace_id', true);
            PERFORM set_config('probity.redacting', 'off', true);
        END $$;
    """)
    for fn in ("probity_redact_audit(integer, jsonb)", "probity_redact_evidence(text, text, text, jsonb)"):
        op.execute(f"REVOKE ALL ON FUNCTION {fn} FROM PUBLIC")
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'probity_app') THEN
                GRANT SELECT, INSERT, UPDATE ON privacy_redactions TO probity_app;
                GRANT USAGE, SELECT ON SEQUENCE privacy_redactions_id_seq TO probity_app;
                GRANT EXECUTE ON FUNCTION probity_redact_audit(integer, jsonb) TO probity_app;
                GRANT EXECUTE ON FUNCTION probity_redact_evidence(text, text, text, jsonb) TO probity_app;
            END IF;
        END $$;
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS probity_redact_evidence(text, text, text, jsonb)")
    op.execute("DROP FUNCTION IF EXISTS probity_redact_audit(integer, jsonb)")
    op.execute("""
        CREATE OR REPLACE FUNCTION probity_forbid_change() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% is immutable (% blocked)', TG_TABLE_NAME, TG_OP;
        END $$;
    """)
    op.drop_index(op.f('ix_privacy_redactions_workspace_id'), table_name='privacy_redactions')
    op.drop_table('privacy_redactions')
