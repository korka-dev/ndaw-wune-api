"""Ajouter photos_classe_url (liste JSON) à rapports_journalier

Revision ID: a56d5527746c
Revises: c3d4e5f6a7b8
Create Date: 2026-06-13

Permet de stocker jusqu'à plusieurs photos (uploadées depuis la galerie)
en plus de l'ancien champ `photo_classe_url` (1 seule photo, conservé
pour compatibilité avec les anciens rapports).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a56d5527746c"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rapports_journalier",
        sa.Column("photos_classe_url", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("rapports_journalier", "photos_classe_url")
