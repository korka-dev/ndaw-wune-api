"""Ajouter groupe_lecture, groupe_maths, statut_selection aux élèves

Revision ID: f5a6b7c8d9e0
Revises: c2d3e4f5a6b7
Create Date: 2026-08-04

BaseNWVFinale2026.xlsx assigne à chaque élève un groupe de méthode de lecture
(Lettres/Syllabes vs Mots/Phrases), un groupe de méthode de maths (Nombres vs
Opérations), et un statut d'échantillon RCT (Titulaire vs Remplaçant). Ces
champs n'ont pas d'équivalent existant sur Eleve — à ne pas confondre avec
User.groupe_recherche (Traitement/Contrôle minuteur), qui est un dispositif
RCT distinct côté enseignant.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f5a6b7c8d9e0"
down_revision = "c2d3e4f5a6b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("eleves", sa.Column("groupe_lecture", sa.String(length=30), nullable=True))
    op.add_column("eleves", sa.Column("groupe_maths", sa.String(length=30), nullable=True))
    op.add_column("eleves", sa.Column("statut_selection", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("eleves", "statut_selection")
    op.drop_column("eleves", "groupe_maths")
    op.drop_column("eleves", "groupe_lecture")
