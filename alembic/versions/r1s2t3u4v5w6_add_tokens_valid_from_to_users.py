"""Ajouter la colonne tokens_valid_from à users

Revision ID: r1s2t3u4v5w6
Revises: q0r1s2t3u4v5
Create Date: 2026-07-31

Permet d'invalider tous les tokens (access + refresh, tous appareils) émis
avant une certaine date pour un utilisateur — utilisé au changement/reset de
mot de passe pour empêcher un refresh token déjà volé de survivre à la
remédiation (voir app/api/routes/auth.py change_password/reset_password et
app/core/deps.py get_current_user).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "r1s2t3u4v5w6"
down_revision = "q0r1s2t3u4v5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("tokens_valid_from", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "tokens_valid_from")
