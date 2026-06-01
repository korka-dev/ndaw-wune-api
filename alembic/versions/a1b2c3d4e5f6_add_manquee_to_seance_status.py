"""Ajouter la valeur manquee au type enum seance_status

Revision ID: a1b2c3d4e5f6
Revises: f3a1c8e2b945
Create Date: 2026-06-01

Ajoute 'manquee' à l'enum PostgreSQL seance_status.
Les séances manquées sont des créneaux planifiés non démarrés après leur heure de fin.
"""
from __future__ import annotations

from alembic import op


def upgrade() -> None:
    # PostgreSQL permet d'ajouter une valeur à un enum existant sans recréer le type.
    # IF NOT EXISTS évite l'erreur si la migration est rejouée.
    op.execute("ALTER TYPE seance_status ADD VALUE IF NOT EXISTS 'manquee'")


def downgrade() -> None:
    # PostgreSQL ne permet pas de supprimer une valeur d'un enum sans recréer le type.
    # En pratique : supprimer d'abord toutes les lignes avec status='manquee',
    # recréer le type sans cette valeur, puis recaster la colonne.
    # On laisse cette migration irréversible pour simplifier.
    pass
