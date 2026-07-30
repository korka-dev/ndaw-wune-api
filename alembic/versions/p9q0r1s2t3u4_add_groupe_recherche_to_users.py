"""Ajouter la colonne groupe_recherche à users

Revision ID: p9q0r1s2t3u4
Revises: o8p9q0r1s2t3
Create Date: 2026-07-28

Groupe de l'étude RCT affecté à un enseignant — "traitement" (app avec
minuteur) ou "controle" (app sans minuteur), importé depuis
Assignation_Traitement_Simple_NWV2026.xlsx.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "p9q0r1s2t3u4"
down_revision = "o8p9q0r1s2t3"
branch_labels = None
depends_on = None

user_groupe_enum = sa.Enum("traitement", "controle", name="user_groupe")


def upgrade() -> None:
    user_groupe_enum.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "users",
        sa.Column(
            "groupe_recherche",
            sa.Enum("traitement", "controle", name="user_groupe", create_type=False),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "groupe_recherche")
    user_groupe_enum.drop(op.get_bind(), checkfirst=True)
