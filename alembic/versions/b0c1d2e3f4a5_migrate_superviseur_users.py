"""Migrer les utilisateurs coordonnateur/title=superviseur vers le rôle superviseur

Revision ID: b0c1d2e3f4a5
Revises: a9f1b2c3d4e5
Create Date: 2026-05-23

Note PostgreSQL :
    Cette migration s'exécute dans une transaction séparée de celle qui a
    ajouté la valeur 'superviseur' à l'ENUM (migration a9f1b2c3d4e5).
    PostgreSQL exige ce découpage : une nouvelle valeur ENUM doit être committée
    avant de pouvoir être utilisée dans un UPDATE.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = 'b0c1d2e3f4a5'
down_revision = 'a9f1b2c3d4e5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Migrer les coordonnateurs ayant title='superviseur' vers le nouveau rôle.
    # Ces utilisateurs étaient créés comme coordonnateurs mais utilisent l'app
    # mobile en tant que superviseurs de terrain.
    op.execute(sa.text(
        """
        UPDATE users
           SET role = 'superviseur'
         WHERE role = 'coordonnateur'
           AND title = 'superviseur'
        """
    ))


def downgrade() -> None:
    # Remettre les superviseurs comme coordonnateurs avant que
    # la migration a9f1b2c3d4e5 supprime la valeur de l'ENUM.
    op.execute(sa.text(
        """
        UPDATE users
           SET role = 'coordonnateur',
               title = 'superviseur'
         WHERE role = 'superviseur'
        """
    ))
