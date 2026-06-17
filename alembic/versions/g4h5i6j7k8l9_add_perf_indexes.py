"""Indexes de performance — rapports_journalier + seances

Revision ID: g4h5i6j7k8l9
Revises: 9f1e2d3c4b5a
Create Date: 2026-06-16

Ajoute les index manquants qui causent des full-table-scans :
  - rapports_journalier.date_rapport  (plages de dates, ORDER BY)
  - rapports_journalier.created_at    (tri par défaut)
  - rapports_journalier.nom_tuteur    (recherche ilike)
  - rapports_journalier.ecole         (recherche ilike)
  - rapports_journalier.ief           (recherche ilike)
"""
from __future__ import annotations

from alembic import op

revision = "g4h5i6j7k8l9"
down_revision = "9f1e2d3c4b5a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Tri et filtre par date — utilisés sur TOUTES les requêtes admin + pagination
    op.create_index(
        "ix_rapports_journalier_date_rapport",
        "rapports_journalier",
        ["date_rapport"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_rapports_journalier_created_at",
        "rapports_journalier",
        ["created_at"],
        unique=False,
        if_not_exists=True,
    )
    # Colonnes utilisées dans les ORDER BY imbriqués et les filtres ilike
    op.create_index(
        "ix_rapports_journalier_nom_tuteur",
        "rapports_journalier",
        ["nom_tuteur"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_rapports_journalier_ecole",
        "rapports_journalier",
        ["ecole"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_rapports_journalier_ief",
        "rapports_journalier",
        ["ief"],
        unique=False,
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_rapports_journalier_ief",         table_name="rapports_journalier")
    op.drop_index("ix_rapports_journalier_ecole",       table_name="rapports_journalier")
    op.drop_index("ix_rapports_journalier_nom_tuteur",  table_name="rapports_journalier")
    op.drop_index("ix_rapports_journalier_created_at",  table_name="rapports_journalier")
    op.drop_index("ix_rapports_journalier_date_rapport", table_name="rapports_journalier")
