"""Ajouter la table evaluation_resultats (une évaluation par jour et par élève)

Revision ID: a0b1c2d3e4f5
Revises: z9a0b1c2d3e4
Create Date: 2026-08-01

Le superviseur doit pouvoir évaluer le même élève chaque jour. Jusqu'ici le
résultat était une colonne unique de `evaluation_tirages` : réévaluer écrasait
la mesure précédente et toute progression était perdue. Chaque passage est
désormais historisé ici (une ligne par tirage et par date), le tirage
conservant le dernier résultat pour l'affichage courant.

L'historique existant est repris : chaque tirage déjà évalué donne une ligne
correspondant à sa date d'évaluation.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "a0b1c2d3e4f5"
down_revision = "z9a0b1c2d3e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evaluation_resultats",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tirage_id", UUID(as_uuid=True),
                  sa.ForeignKey("evaluation_tirages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("superviseur_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("date_eval", sa.Date(), nullable=False),
        sa.Column("resultat", sa.String(30), nullable=False),
        sa.Column("commentaire", sa.Text(), nullable=True),
        sa.Column("audio_filename", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tirage_id", "date_eval", name="uq_evaluation_resultat_tirage_date"),
    )
    op.create_index("ix_evaluation_resultats_tirage_id", "evaluation_resultats", ["tirage_id"])
    op.create_index("ix_evaluation_resultats_date_eval", "evaluation_resultats", ["date_eval"])

    # Reprise de l'historique déjà saisi : une ligne par tirage évalué.
    op.execute(
        """
        INSERT INTO evaluation_resultats
            (id, tirage_id, superviseur_id, date_eval, resultat, commentaire,
             audio_filename, created_at, updated_at)
        SELECT gen_random_uuid(), t.id, t.superviseur_id,
               COALESCE(t.date_eval, CURRENT_DATE), t.resultat, t.commentaire,
               t.audio_filename, NOW(), NOW()
          FROM evaluation_tirages t
         WHERE t.resultat IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_index("ix_evaluation_resultats_date_eval", table_name="evaluation_resultats")
    op.drop_index("ix_evaluation_resultats_tirage_id", table_name="evaluation_resultats")
    op.drop_table("evaluation_resultats")
