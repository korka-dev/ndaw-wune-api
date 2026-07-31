"""Ajouter la colonne langue à documents

Revision ID: x7y8z9a0b1c2
Revises: r1s2t3u4v5w6
Create Date: 2026-07-31

Langue nationale à laquelle un document (ressource pédagogique) s'adresse
(ex: pulaar, wolof, sereer). NULL = visible pour toutes les langues.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "x7y8z9a0b1c2"
down_revision = "r1s2t3u4v5w6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("langue", sa.String(50), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "langue")
