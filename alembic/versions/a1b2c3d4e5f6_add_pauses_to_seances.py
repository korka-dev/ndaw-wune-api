"""add pauses and total_paused_minutes to seances

Revision ID: a1b2c3d4e5f6
Revises: f3a1c8e2b945
Create Date: 2026-05-20 00:00:00.000000

"""
from __future__ import annotations

from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = "f3a1c8e2b945"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE seances ADD COLUMN IF NOT EXISTS pauses JSONB NOT NULL DEFAULT '[]'::jsonb"
    )
    op.execute(
        "ALTER TABLE seances ADD COLUMN IF NOT EXISTS total_paused_minutes INTEGER"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE seances DROP COLUMN IF EXISTS total_paused_minutes")
    op.execute("ALTER TABLE seances DROP COLUMN IF EXISTS pauses")
