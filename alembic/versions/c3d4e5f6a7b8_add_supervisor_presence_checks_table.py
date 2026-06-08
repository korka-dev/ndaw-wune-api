"""Ajouter la table supervisor_presence_checks

Revision ID: c3d4e5f6a7b8
Revises: f7e8d9c0b1a2
Create Date: 2026-06-08

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "c3d4e5f6a7b8"
down_revision = "f7e8d9c0b1a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "supervisor_presence_checks",
        sa.Column("id",             UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("created_at",     sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at",     sa.DateTime(timezone=True), nullable=False),
        sa.Column("superviseur_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("teacher_id",     UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("date_jour",      sa.Date,        nullable=False, index=True),
        sa.Column("present",        sa.Boolean,     nullable=False),
        sa.Column("motif",          sa.String(200), nullable=True),
        sa.UniqueConstraint(
            "superviseur_id", "teacher_id", "date_jour",
            name="uq_sup_presence_superviseur_teacher_date",
        ),
    )


def downgrade() -> None:
    op.drop_table("supervisor_presence_checks")
