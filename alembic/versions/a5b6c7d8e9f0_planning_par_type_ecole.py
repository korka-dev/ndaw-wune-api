"""Lot L11 — un emploi du temps par type d'école (avec / sans FLS)

Revision ID: a5b6c7d8e9f0
Revises: z4a5b6c7d8e9
Create Date: 2026-11-26

- planning_segments.type_ecole : « tous » (toutes les écoles, comportement
  antérieur), « fls » (écoles avec FLS) ou « sans_fls » (écoles sans FLS).
- Les créneaux déjà réservés FLS (lot L9) deviennent « fls ».
- L'unicité (campagne, semaine, jour, heure de début) inclut le type : les
  deux emplois du temps commencent tous deux à 16 h 00.
Aucun créneau n'est supprimé.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a5b6c7d8e9f0"
down_revision: Union[str, None] = "z4a5b6c7d8e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("planning_segments",
                  sa.Column("type_ecole", sa.String(10), nullable=False, server_default="tous"))
    op.execute(sa.text("UPDATE planning_segments SET type_ecole = 'fls' WHERE reserve_fls"))
    op.create_check_constraint("ck_planning_type_ecole", "planning_segments",
                               "type_ecole IN ('tous', 'fls', 'sans_fls')")
    op.drop_constraint("uq_planning_segment", "planning_segments", type_="unique")
    op.create_unique_constraint("uq_planning_segment", "planning_segments",
                                ["session_id", "semaine", "jour", "heure_debut", "type_ecole"])


def downgrade() -> None:
    n = op.get_bind().execute(sa.text(
        "SELECT COUNT(*) FROM planning_segments WHERE type_ecole = 'sans_fls'")).scalar()
    if n:
        raise RuntimeError(f"{n} créneau(x) « sans FLS » : supprimez-les avant de revenir en arrière.")
    op.drop_constraint("uq_planning_segment", "planning_segments", type_="unique")
    op.create_unique_constraint("uq_planning_segment", "planning_segments",
                                ["session_id", "semaine", "jour", "heure_debut"])
    op.drop_constraint("ck_planning_type_ecole", "planning_segments", type_="check")
    op.drop_column("planning_segments", "type_ecole")
