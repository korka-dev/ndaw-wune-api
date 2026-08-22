"""Ajouter semaine_debut, semaine_fin aux dossiers d'évaluation

Revision ID: p4q5r6s7t8u9
Revises: n3wr2mrq
Create Date: 2026-08-21

Permet à l'admin d'assigner un dossier d'évaluation (EGRA) à une plage de
semaines du programme, cohérente avec ProgressionConfig.nb_semaines — par
exemple un dossier plus simple pour les semaines 1-3, un dossier plus
difficile pour les semaines 4-10. NULL = valable pour toutes les semaines
(comportement historique, aucun dossier existant n'est impacté).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "p4q5r6s7t8u9"
down_revision = "n3wr2mrq"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("evaluation_docs", sa.Column("semaine_debut", sa.Integer(), nullable=True))
    op.add_column("evaluation_docs", sa.Column("semaine_fin", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("evaluation_docs", "semaine_fin")
    op.drop_column("evaluation_docs", "semaine_debut")
