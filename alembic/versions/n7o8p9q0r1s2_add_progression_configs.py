"""Ajouter la table progression_configs

Revision ID: n7o8p9q0r1s2
Revises: m6n7o8p9q0r1
Create Date: 2026-07-28

Permet à l'admin de configurer le nombre de semaines/jours du programme par
école et/ou par session (remplace les constantes codées en dur 25/7 côté
app mobile). Une ligne sans école ni session sert de repli global.
"""
from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "n7o8p9q0r1s2"
down_revision = "m6n7o8p9q0r1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "progression_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("school_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("schools.id", ondelete="CASCADE"), nullable=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("program_sessions.id", ondelete="CASCADE"), nullable=True),
        sa.Column("nb_semaines", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("nb_jours", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("school_id", "session_id", name="uq_progression_config_school_session"),
    )
    op.create_index("ix_progression_configs_school_id", "progression_configs", ["school_id"])
    op.create_index("ix_progression_configs_session_id", "progression_configs", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_progression_configs_session_id", table_name="progression_configs")
    op.drop_index("ix_progression_configs_school_id", table_name="progression_configs")
    op.drop_table("progression_configs")
