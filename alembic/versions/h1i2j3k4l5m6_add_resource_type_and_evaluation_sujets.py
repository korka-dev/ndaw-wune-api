"""Ajouter resource_type aux documents + tables evaluation_sujets / evaluation_tirages

Revision ID: h1i2j3k4l5m6
Revises: g4h5i6j7k8l9
Create Date: 2026-06-25

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "h1i2j3k4l5m6"
down_revision = "g4h5i6j7k8l9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Colonne resource_type dans documents ────────────────────────────────
    op.add_column(
        "documents",
        sa.Column("resource_type", sa.String(20), nullable=False, server_default="document"),
    )

    # ── 2. Table evaluation_sujets ────────────────────────────────────────────
    op.create_table(
        "evaluation_sujets",
        sa.Column("id",                   UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("created_at",           sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at",           sa.DateTime(timezone=True), nullable=False),
        sa.Column("titre",                sa.String(255), nullable=False),
        sa.Column("description",          sa.Text, nullable=True),
        sa.Column("nb_eleves_par_classe", sa.Integer, nullable=False, server_default="5"),
        sa.Column("session_id",           UUID(as_uuid=True),
                  sa.ForeignKey("program_sessions.id", ondelete="SET NULL"),
                  nullable=True, index=True),
        sa.Column("created_by",           UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
    )

    # ── 3. Table evaluation_tirages ───────────────────────────────────────────
    op.create_table(
        "evaluation_tirages",
        sa.Column("id",             UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("created_at",     sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at",     sa.DateTime(timezone=True), nullable=False),
        sa.Column("sujet_id",       UUID(as_uuid=True),
                  sa.ForeignKey("evaluation_sujets.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("eleve_id",       UUID(as_uuid=True),
                  sa.ForeignKey("eleves.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("superviseur_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True, index=True),
        sa.Column("resultat",       sa.String(20),  nullable=True),
        sa.Column("commentaire",    sa.String(500), nullable=True),
        sa.Column("date_eval",      sa.Date,        nullable=True, index=True),
        sa.Column("audio_filename", sa.String(512), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("evaluation_tirages")
    op.drop_table("evaluation_sujets")
    op.drop_column("documents", "resource_type")
