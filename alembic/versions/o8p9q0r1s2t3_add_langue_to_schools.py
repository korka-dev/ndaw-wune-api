"""Ajouter la colonne langue à schools

Revision ID: o8p9q0r1s2t3
Revises: n7o8p9q0r1s2
Create Date: 2026-07-28

Langue nationale d'enseignement de l'école (ex: pulaar, wolof, sereer),
importée depuis Langues.xlsx.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "o8p9q0r1s2t3"
down_revision = "n7o8p9q0r1s2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("schools", sa.Column("langue", sa.String(50), nullable=True))


def downgrade() -> None:
    op.drop_column("schools", "langue")
