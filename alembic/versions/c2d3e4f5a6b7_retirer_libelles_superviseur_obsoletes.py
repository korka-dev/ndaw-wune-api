"""Retirer les libellés superviseur devenus obsolètes

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-08-02

Le rapport de supervision ne comporte plus que les cinq questions à réponse
libre (date, tuteur, points forts, points à améliorer, recommandations). Les
champs « classes ayant terminé », « incidents signalés », « bilan global » et
« commentaire » ont été retirés du formulaire mobile : leurs libellés n'ont
donc plus lieu d'apparaître dans le dashboard, où ils laisseraient croire à
l'admin qu'il configure des questions encore posées.

Les rapports déjà soumis ne sont pas touchés : leur contenu est du texte figé.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "c2d3e4f5a6b7"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None

# (clé, cible, texte d'origine) — le texte sert à restaurer en cas de downgrade.
_OBSOLETES = [
    ("superviseur.classes_terminees_label", "superviseur", "Classes ayant terminé leur planning"),
    ("superviseur.incidents_question",      "superviseur", "Incidents signalés ?"),
    ("superviseur.bilan_label",             "superviseur", "Bilan global"),
    ("superviseur.commentaire_label",       "superviseur", "Commentaire"),
]


def upgrade() -> None:
    op.execute(
        sa.text("DELETE FROM rapport_libelles WHERE cle = ANY(:cles)").bindparams(
            sa.bindparam("cles", value=[cle for cle, _, _ in _OBSOLETES])
        )
    )


def downgrade() -> None:
    table = sa.table(
        "rapport_libelles",
        sa.column("id", UUID(as_uuid=True)),
        sa.column("cle", sa.String),
        sa.column("cible", sa.String),
        sa.column("texte", sa.Text),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    now = datetime.now(timezone.utc)
    op.bulk_insert(table, [
        {
            "id": uuid.uuid4(), "cle": cle, "cible": cible, "texte": texte,
            "created_at": now, "updated_at": now,
        }
        for cle, cible, texte in _OBSOLETES
    ])
