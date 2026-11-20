"""Idempotence et clôture des séances (rapport V2, APP-16/18/21)

Revision ID: s7t8u9v0w1x2
Revises: r6s7t8u9v0w1
Create Date: 2026-11-20

- rapports_journalier : client_op_id + client_op_hash, unicité (auteur, clé).
- seances : client_seance_id, unicité (auteur, clé) ; cloture_mode.
- Séances clôturées automatiquement : la durée artificielle (début + 4 h) est
  retirée (duree_minutes et finished_at à NULL, cloture_mode = automatique).
  RÉVERSIBLE : le downgrade la recalcule exactement comme l'ancien code
  (finished_at = started_at + 4 h ; durée = 240 − pauses).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "s7t8u9v0w1x2"
down_revision = "r6s7t8u9v0w1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("rapports_journalier", sa.Column("client_op_id", sa.String(64), nullable=True))
    op.add_column("rapports_journalier", sa.Column("client_op_hash", sa.String(64), nullable=True))
    op.create_unique_constraint(
        "uq_rapports_journalier_teacher_client_op", "rapports_journalier", ["teacher_id", "client_op_id"]
    )

    op.add_column("seances", sa.Column("client_seance_id", sa.String(64), nullable=True))
    op.add_column("seances", sa.Column("cloture_mode", sa.String(20), nullable=True))
    op.create_unique_constraint(
        "uq_seances_teacher_client_seance", "seances", ["teacher_id", "client_seance_id"]
    )

    op.execute(
        "UPDATE seances SET cloture_mode = 'automatique', duree_minutes = NULL, finished_at = NULL "
        "WHERE auto_closed = true"
    )
    op.execute(
        "UPDATE seances SET cloture_mode = 'tuteur' "
        "WHERE status = 'terminee' AND auto_closed = false AND cloture_mode IS NULL"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE seances SET finished_at = started_at + interval '4 hours', "
        "duree_minutes = 240 - COALESCE(total_paused_minutes, 0) "
        "WHERE cloture_mode = 'automatique' AND started_at IS NOT NULL"
    )
    # Une récupération manuelle redevient une clôture automatique (ancien modèle).
    op.execute(
        "UPDATE seances SET auto_closed = true, finished_at = started_at + interval '4 hours', "
        "duree_minutes = 240 - COALESCE(total_paused_minutes, 0) "
        "WHERE cloture_mode = 'recuperation' AND started_at IS NOT NULL"
    )
    op.drop_constraint("uq_seances_teacher_client_seance", "seances", type_="unique")
    op.drop_column("seances", "cloture_mode")
    op.drop_column("seances", "client_seance_id")
    op.drop_constraint("uq_rapports_journalier_teacher_client_op", "rapports_journalier", type_="unique")
    op.drop_column("rapports_journalier", "client_op_hash")
    op.drop_column("rapports_journalier", "client_op_id")
