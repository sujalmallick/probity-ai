"""baseline approval on past invoices and purchase orders (only approved rows feed comparisons)

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-04 23:30:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("historical_invoices", "purchase_orders"):
        op.add_column(table, sa.Column("source", sa.String(length=20), nullable=False, server_default="import"))
        op.add_column(table, sa.Column("entered_by", sa.String(length=40), nullable=True))
        op.add_column(table, sa.Column("approved_by", sa.String(length=40), nullable=True))
        op.add_column(table, sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))
    # Rows written by a decided case were approved by that decision. Everything else waits for an approver.
    op.execute("UPDATE historical_invoices SET source = 'case', approved_by = 'case_decision', approved_at = now() WHERE case_id IS NOT NULL")


def downgrade() -> None:
    for table in ("historical_invoices", "purchase_orders"):
        for col in ("approved_at", "approved_by", "entered_by", "source"):
            op.drop_column(table, col)
