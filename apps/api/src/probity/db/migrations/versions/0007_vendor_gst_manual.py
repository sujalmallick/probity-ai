"""vendor GST status entered manually by a user (never registry-verified)

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-04 20:10:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('vendors', sa.Column('gst_manual', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column('vendors', 'gst_manual')
