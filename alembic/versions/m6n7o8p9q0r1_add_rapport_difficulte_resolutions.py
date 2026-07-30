"""Ajouter la table rapport_difficulte_resolutions

Revision ID: m6n7o8p9q0r1
Revises: l5m6n7o8p9q0
Create Date: 2026-07-28

Permet au superviseur (mobile + admin web) de marquer une difficulté
signalée dans un rapport journalier comme résolue, difficulté par
difficulté (une ligne par (rapport, libellé)).
"""
from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "m6n7o8p9q0r1"
down_revision = "l5m6n7o8p9q0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rapport_difficulte_resolutions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("rapport_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("rapports_journalier.id", ondelete="CASCADE"), nullable=False),
        sa.Column("difficulte_label", sa.String(500), nullable=False),
        sa.Column("resolue", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("resolue_par", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("resolue_le", sa.DateTime(timezone=True), nullable=True),
        sa.Column("commentaire_resolution", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("rapport_id", "difficulte_label", name="uq_rapport_difficulte_resolution"),
    )
    op.create_index(
        "ix_rapport_difficulte_resolutions_rapport_id",
        "rapport_difficulte_resolutions",
        ["rapport_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_rapport_difficulte_resolutions_rapport_id", table_name="rapport_difficulte_resolutions")
    op.drop_table("rapport_difficulte_resolutions")
