"""Ajouter la table eleve_remplacements

Revision ID: q0r1s2t3u4v5
Revises: p9q0r1s2t3u4
Create Date: 2026-07-28

Historique des remplacements d'élève effectués par un tuteur (avec motif),
visible sur le dashboard admin. Le remplacement a un effet immédiat : le
nouvel élève est créé actif, l'ancien passe en statut "inactif" (voir
app/api/routes/app/remplacements.py).
"""
from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "q0r1s2t3u4v5"
down_revision = "p9q0r1s2t3u4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "eleve_remplacements",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("ancien_eleve_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("eleves.id", ondelete="SET NULL"), nullable=True),
        sa.Column("ancien_eleve_nom", sa.String(200), nullable=True),
        sa.Column("nouveau_eleve_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("eleves.id", ondelete="SET NULL"), nullable=True),
        sa.Column("nouveau_eleve_nom", sa.String(200), nullable=False),
        sa.Column("motif", sa.Text(), nullable=False),
        sa.Column("teacher_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("school_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("schools.id", ondelete="SET NULL"), nullable=True),
        sa.Column("classe", sa.String(50), nullable=False),
        sa.Column("date_remplacement", sa.Date(), nullable=False, server_default=sa.func.current_date()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_eleve_remplacements_teacher_id", "eleve_remplacements", ["teacher_id"])
    op.create_index("ix_eleve_remplacements_school_id", "eleve_remplacements", ["school_id"])


def downgrade() -> None:
    op.drop_index("ix_eleve_remplacements_school_id", table_name="eleve_remplacements")
    op.drop_index("ix_eleve_remplacements_teacher_id", table_name="eleve_remplacements")
    op.drop_table("eleve_remplacements")
