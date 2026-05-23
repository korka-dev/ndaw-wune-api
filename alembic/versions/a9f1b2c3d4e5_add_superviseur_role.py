"""Ajouter la valeur 'superviseur' au type ENUM user_role

Revision ID: a9f1b2c3d4e5
Revises: f3a1c8e2b945
Create Date: 2026-05-23

Contexte :
    Jusqu'ici les superviseurs terrain étaient stockés avec le rôle
    'coordonnateur', ce qui créait une ambiguïté avec les coordonnateurs
    admin et empêchait le bon routage dans l'application mobile.
    On ajoute la valeur 'superviseur' à l'enum PostgreSQL et on migre
    les enregistrements concernés (ceux avec title = 'superviseur' ou
    dont l'accès était géré comme superviseur mobile).

Note PostgreSQL :
    ALTER TYPE … ADD VALUE ne peut pas être annulé dans la même transaction
    que la création de la valeur. Le downgrade supprime les lignes d'abord
    pour pouvoir recréer le type sans la valeur.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# Variables obligatoires pour Alembic
revision = 'a9f1b2c3d4e5'
down_revision = 'f3a1c8e2b945'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Ajouter la valeur à l'ENUM (PostgreSQL 12+ : IF NOT EXISTS) ────────
    op.execute("ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'superviseur'")

    # ── 2. Migrer les coordonnateurs ayant title='superviseur' ─────────────────
    # Ces utilisateurs étaient créés comme coordonnateurs mais utilisent l'app
    # mobile en tant que superviseurs de terrain.
    op.execute(
        """
        UPDATE users
           SET role = 'superviseur'
         WHERE role = 'coordonnateur'
           AND title = 'superviseur'
        """
    )


def downgrade() -> None:
    # Remettre les superviseurs comme coordonnateurs avant de supprimer la valeur
    op.execute(
        """
        UPDATE users
           SET role = 'coordonnateur', title = 'superviseur'
         WHERE role = 'superviseur'
        """
    )

    # PostgreSQL ne permet pas de supprimer une valeur d'un ENUM existant.
    # On recrée le type en trois étapes :
    #   1. Renommer l'ancien type
    #   2. Créer le nouveau type sans 'superviseur'
    #   3. Modifier la colonne pour utiliser le nouveau type
    #   4. Supprimer l'ancien type
    op.execute("ALTER TYPE user_role RENAME TO user_role_old")
    op.execute("CREATE TYPE user_role AS ENUM ('admin', 'coordonnateur', 'enseignant')")
    op.execute(
        """
        ALTER TABLE users
          ALTER COLUMN role TYPE user_role
          USING role::text::user_role
        """
    )
    op.execute("DROP TYPE user_role_old")
