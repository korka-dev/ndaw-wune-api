"""Lot L9 — écoles avec / sans FLS et créneaux réservés FLS

Revision ID: z4a5b6c7d8e9
Revises: y3z4a5b6c7d8
Create Date: 2026-11-25

- schools.fls (NULL = non renseigné : rien ne change pour l'école) ;
- planning_segments.reserve_fls, mis à vrai pour les créneaux existants
  « Français langue seconde » / « FLS ». Aucun créneau n'est supprimé.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "z4a5b6c7d8e9"
down_revision: Union[str, None] = "y3z4a5b6c7d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("schools", sa.Column("fls", sa.Boolean(), nullable=True))
    op.add_column("planning_segments",
                  sa.Column("reserve_fls", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.execute(sa.text(r"""
        UPDATE planning_segments SET reserve_fls = true
        WHERE matiere ILIKE '%langue seconde%' OR matiere ~* '(^|[^[:alpha:]])fls([^[:alpha:]]|$)'
    """))


def downgrade() -> None:
    op.drop_column("planning_segments", "reserve_fls")
    op.drop_column("schools", "fls")
