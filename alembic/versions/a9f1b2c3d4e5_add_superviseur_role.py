"""Ajouter la valeur 'superviseur' au type ENUM user_role

Revision ID: a9f1b2c3d4e5
Revises: d8e9f0a1b2c3
Create Date: 2026-05-23

Note PostgreSQL :
    ALTER TYPE … ADD VALUE doit être commité dans sa propre transaction
    avant que la nouvelle valeur puisse être utilisée (même session).
    La migration des données (UPDATE) est donc dans la migration suivante :
    b0c1d2e3f4a5_migrate_superviseur_users.py
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = 'a9f1b2c3d4e5'
down_revision = 'd8e9f0a1b2c3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Ajouter la valeur à l'ENUM — cette transaction est committée seule,
    # ce qui permet à la migration suivante d'utiliser 'superviseur'.
    op.execute(sa.text("ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'superviseur'"))


def downgrade() -> None:
    # La suppression de la valeur ENUM et la migration des données
    # sont gérées dans la migration b0c1d2e3f4a5 (downgrade en premier).
    # Ici on recrée le type sans 'superviseur' si nécessaire.
    op.execute(sa.text("ALTER TYPE user_role RENAME TO user_role_old"))
    op.execute(sa.text("CREATE TYPE user_role AS ENUM ('admin', 'coordonnateur', 'enseignant')"))
    op.execute(sa.text(
        """
        ALTER TABLE users
          ALTER COLUMN role TYPE user_role
          USING role::text::user_role
        """
    ))
    op.execute(sa.text("DROP TYPE user_role_old"))
