"""Ajouter le libellé « Jour de cours » (étape 1 du rapport journalier)

Revision ID: z9a0b1c2d3e4
Revises: y8z9a0b1c2d3
Create Date: 2026-08-01

L'étape 1/4 du rapport journalier du tuteur (semaine + jour de cours) était en
partie codée en dur côté app mobile : le libellé « Jour de cours » n'était pas
éditable depuis le dashboard admin, contrairement aux autres champs de l'étape.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "z9a0b1c2d3e4"
down_revision = "y8z9a0b1c2d3"
branch_labels = None
depends_on = None

_DEFAULTS = [
    ("tuteur.jour_cours_label", "tuteur", "Jour de cours"),
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
    existing = {
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
        if cle not in existing
    ]
    if rows:
        op.bulk_insert(table, rows)


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM rapport_libelles WHERE cle = ANY(:cles)").bindparams(
            sa.bindparam("cles", value=[cle for cle, _, _ in _DEFAULTS])
        )
    )
