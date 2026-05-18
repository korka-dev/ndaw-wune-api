"""photo_classe_url: String(500) → Text

Revision ID: f3a1c8e2b945
Revises: d2bfed0fb6d1
Create Date: 2026-05-18
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "f3a1c8e2b945"
down_revision = "d2bfed0fb6d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "rapports_journalier",
        "photo_classe_url",
        type_=sa.Text(),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "rapports_journalier",
        "photo_classe_url",
        type_=sa.String(500),
        existing_nullable=True,
    )
