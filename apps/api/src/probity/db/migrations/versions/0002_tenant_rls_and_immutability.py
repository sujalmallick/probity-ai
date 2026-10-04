"""Row-level security per workspace, append-only audit log, immutable evidence.

Revision ID: 0002
Revises: 0001
"""
from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# Every table holding tenant data. (workspaces, users and invitations are resolved before a tenant is
# known — at sign-in — and are always filtered by id/workspace in application code.)
TENANT_TABLES = [
    "vendors", "vendor_bank_accounts", "vendor_domains", "vendor_contacts", "historical_invoices", "purchase_orders",
    "documents", "cases", "evidence", "claims", "risk_scores", "decisions", "drafts", "messages", "case_memory",
    "audit_log", "graph_edges", "agent_events", "llm_calls",
]


def upgrade() -> None:
    for t in TENANT_TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")  # applies to the table owner too
        op.execute(
            f"CREATE POLICY tenant_isolation ON {t} "
            f"USING (workspace_id = current_setting('app.workspace_id', true)) "
            f"WITH CHECK (workspace_id = current_setting('app.workspace_id', true))"
        )

    # Append-only audit log and immutable evidence, enforced by the database regardless of role.
    op.execute("""
        CREATE FUNCTION probity_forbid_change() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% is immutable (% blocked)', TG_TABLE_NAME, TG_OP;
        END $$;
    """)
    op.execute("CREATE TRIGGER audit_log_append_only BEFORE UPDATE OR DELETE ON audit_log FOR EACH ROW EXECUTE FUNCTION probity_forbid_change()")
    op.execute("CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log FOR EACH STATEMENT EXECUTE FUNCTION probity_forbid_change()")
    op.execute("CREATE TRIGGER evidence_immutable BEFORE UPDATE ON evidence FOR EACH ROW EXECUTE FUNCTION probity_forbid_change()")

    # The inbound-email webhook knows only a case id. This narrowly scoped definer function returns the
    # case's workspace so the request can then run under that tenant's RLS context.
    op.execute("""
        CREATE FUNCTION probity_case_workspace(p_case_id text) RETURNS text
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
            SELECT workspace_id FROM cases WHERE id = p_case_id
        $$;
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS probity_case_workspace(text)")
    op.execute("DROP TRIGGER IF EXISTS evidence_immutable ON evidence")
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_truncate ON audit_log")
    op.execute("DROP TRIGGER IF EXISTS audit_log_append_only ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS probity_forbid_change()")
    for t in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
        op.execute(f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")
