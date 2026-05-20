"""add pauses and total_paused_minutes to seances

Revision ID: a1b2c3d4e5f6
Revises: f3a1c8e2b945
Create Date: 2026-05-20 00:00:00.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "a1b2c3d4e5f6"
down_revision = "f3a1c8e2b945"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "seances",
        sa.Column(
            "pauses",
            JSONB(),
            nullable=False,
            server_default="'[]'::jsonb",
        ),
    )
    op.add_column(
        "seances",
        sa.Column("total_paused_minutes", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("seances", "total_paused_minutes")
    op.drop_column("seances", "pauses")
