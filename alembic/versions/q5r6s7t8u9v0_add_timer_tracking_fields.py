"""Ajouter le suivi détaillé du timer (usage_logs) et la clôture auto (seances)

Revision ID: q5r6s7t8u9v0
Revises: p4q5r6s7t8u9
Create Date: 2026-08-22

- usage_logs.user_code       : code acteur (téléphone), en plus du nom en clair
- usage_logs.seance_id       : relie l'événement timer à la séance concernée
- usage_logs.duration_seconds : durée de séance calculée par l'app, en secondes
- seances.auto_closed        : vrai si clôturée automatiquement après 4h sans
                                timer_stop, plutôt que fermée par l'enseignant
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "q5r6s7t8u9v0"
down_revision = "p4q5r6s7t8u9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("usage_logs", sa.Column("user_code", sa.String(length=30), nullable=True))
    op.add_column("usage_logs", sa.Column("seance_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("usage_logs", sa.Column("duration_seconds", sa.Integer(), nullable=True))
    op.create_index("ix_usage_logs_seance_id", "usage_logs", ["seance_id"])

    op.add_column("seances", sa.Column("auto_closed", sa.Boolean(), nullable=False, server_default="false"))


def downgrade() -> None:
    op.drop_column("seances", "auto_closed")

    op.drop_index("ix_usage_logs_seance_id", table_name="usage_logs")
    op.drop_column("usage_logs", "duration_seconds")
    op.drop_column("usage_logs", "seance_id")
    op.drop_column("usage_logs", "user_code")
