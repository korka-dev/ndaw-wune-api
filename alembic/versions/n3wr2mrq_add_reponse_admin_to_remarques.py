"""Ajouter reponse_admin, reponse_admin_at aux remarques

Revision ID: n3wr2mrq
Revises: f5a6b7c8d9e0
Create Date: 2026-08-10

Permet à l'admin de répondre à une remarque signalée par un tuteur/superviseur
depuis le dashboard (§8 du rapport admin : « Excellent, ajouter la possibilité
de répondre aux remarques »). L'affichage côté app mobile de cette réponse
n'est pas encore fait — voir points ouverts.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "n3wr2mrq"
down_revision = "f5a6b7c8d9e0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("remarques", sa.Column("reponse_admin", sa.Text(), nullable=True))
    op.add_column("remarques", sa.Column("reponse_admin_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("remarques", "reponse_admin_at")
    op.drop_column("remarques", "reponse_admin")
