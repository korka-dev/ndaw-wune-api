"""Ajouter la colonne cible à rapport_questions

Revision ID: d3e4f5a6b7c8
Revises: x7y8z9a0b1c2
Create Date: 2026-07-31

Permet à l'admin de désigner à quel rôle mobile une question complémentaire
s'adresse : "tuteur", "superviseur" ou "tous". Jusqu'ici, ces questions
n'étaient synchronisées et affichées que dans le rapport journalier du
tuteur — cette colonne permet de réutiliser le même système pour le
rapport du superviseur.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d3e4f5a6b7c8"
down_revision = "x7y8z9a0b1c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rapport_questions",
        sa.Column("cible", sa.String(20), nullable=False, server_default="tuteur"),
    )


def downgrade() -> None:
    op.drop_column("rapport_questions", "cible")
