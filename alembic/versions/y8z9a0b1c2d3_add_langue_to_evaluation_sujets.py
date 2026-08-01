"""Ajouter la colonne langue à evaluation_sujets

Revision ID: y8z9a0b1c2d3
Revises: x7y8z9a0b1c2
Create Date: 2026-07-31

Langue d'enseignement à laquelle un sujet d'évaluation s'adresse
(ex: pulaar, wolof, seereer). NULL = visible pour toutes les langues.
Les superviseurs ne voient que les sujets de la langue de leur école.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "y8z9a0b1c2d3"
down_revision = "e4f5a6b7c8d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("evaluation_sujets", sa.Column("langue", sa.String(50), nullable=True))


def downgrade() -> None:
    op.drop_column("evaluation_sujets", "langue")
