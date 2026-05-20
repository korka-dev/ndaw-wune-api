"""add genre, date_naissance, statut to eleves

Revision ID: b3c4d5e6f7a8
Revises: a1b2c3d4e5f6
Create Date: 2026-05-20 00:00:00.000000

"""
from __future__ import annotations

from alembic import op

revision = "b3c4d5e6f7a8"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE eleves ADD COLUMN IF NOT EXISTS genre VARCHAR(20)")
    op.execute("ALTER TABLE eleves ADD COLUMN IF NOT EXISTS date_naissance VARCHAR(10)")
    op.execute("ALTER TABLE eleves ADD COLUMN IF NOT EXISTS statut VARCHAR(20) NOT NULL DEFAULT 'actif'")


def downgrade() -> None:
    op.execute("ALTER TABLE eleves DROP COLUMN IF EXISTS statut")
    op.execute("ALTER TABLE eleves DROP COLUMN IF EXISTS date_naissance")
    op.execute("ALTER TABLE eleves DROP COLUMN IF EXISTS genre")
