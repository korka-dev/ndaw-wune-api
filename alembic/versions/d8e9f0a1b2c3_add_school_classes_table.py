"""add school_classes table

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
Create Date: 2026-05-21

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d8e9f0a1b2c3"
down_revision = "c7d8e9f0a1b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "school_classes",
        sa.Column("id",         postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name",       sa.String(50),  nullable=False),
        sa.Column("niveau",     sa.String(20),  nullable=False),
        sa.Column("effectif",   sa.Integer(),   nullable=True),
        sa.Column("school_id",  postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("schools.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  onupdate=sa.func.now(), nullable=False),
        sa.UniqueConstraint("school_id", "name", name="uq_school_classes_school_name"),
    )
    op.create_index("ix_school_classes_school_id", "school_classes", ["school_id"])


def downgrade() -> None:
    op.drop_index("ix_school_classes_school_id", table_name="school_classes")
    op.drop_table("school_classes")
