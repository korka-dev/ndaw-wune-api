"""Ajouter les libellés du rapport de supervision (date, tuteur, points forts…)

Revision ID: b1c2d3e4f5a6
Revises: a0b1c2d3e4f5
Create Date: 2026-08-02

Cinq questions structurantes du rapport superviseur, à réponse libre. Comme les
autres libellés, leur texte reste modifiable depuis le dashboard admin
(Questions de Rapport → Libellés des rapports) sans toucher au code de l'app.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "b1c2d3e4f5a6"
down_revision = "a0b1c2d3e4f5"
branch_labels = None
depends_on = None

_DEFAULTS = [
    ("superviseur.date_supervision_label", "superviseur", "Quelle est la date de supervision ?"),
    ("superviseur.tuteur_label",           "superviseur", "Quel est le tuteur supervisé ?"),
    ("superviseur.points_forts_label",     "superviseur", "Quels sont les points forts de la supervision ?"),
    ("superviseur.points_ameliorer_label", "superviseur", "Quels sont les points à améliorer ?"),
    ("superviseur.recommandations_label",  "superviseur", "Quelles sont les recommandations ?"),
]


def upgrade() -> None:
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
    conn = op.get_bind()
    existantes = {
        row[0]
        for row in conn.execute(
            sa.text("SELECT cle FROM rapport_libelles WHERE cle = ANY(:cles)"),
            {"cles": [cle for cle, _, _ in _DEFAULTS]},
        )
    }
    rows = [
        {
            "id": uuid.uuid4(),
            "cle": cle,
            "cible": cible,
            "texte": texte,
            "created_at": now,
            "updated_at": now,
        }
        for cle, cible, texte in _DEFAULTS
        if cle not in existantes
    ]
    if rows:
        op.bulk_insert(table, rows)


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM rapport_libelles WHERE cle = ANY(:cles)").bindparams(
            sa.bindparam("cles", value=[cle for cle, _, _ in _DEFAULTS])
        )
    )
