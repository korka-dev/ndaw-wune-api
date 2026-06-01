"""Ajouter la valeur manquee au type enum seance_status

Revision ID: b22e647b0faf
Revises: b0c1d2e3f4a5
Create Date: 2026-06-01

Ajoute 'manquee' à l'enum PostgreSQL seance_status.
Les séances manquées sont des créneaux planifiés non démarrés après leur heure de fin.
"""
from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = 'b22e647b0faf'
down_revision = 'b0c1d2e3f4a5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostgreSQL permet d'ajouter une valeur à un enum existant sans recréer le type.
    # IF NOT EXISTS évite l'erreur si la migration est rejouée.
    op.execute("ALTER TYPE seance_status ADD VALUE IF NOT EXISTS 'manquee'")


def downgrade() -> None:
    # PostgreSQL ne permet pas de supprimer une valeur d'un enum sans recréer le type.
    # On laisse cette migration irréversible pour simplifier.
    pass
