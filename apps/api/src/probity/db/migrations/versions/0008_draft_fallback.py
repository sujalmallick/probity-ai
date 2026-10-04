"""drafts.fallback: marks drafts written from the standard template because the AI was unavailable

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-04 22:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('drafts', sa.Column('fallback', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column('drafts', 'fallback')
