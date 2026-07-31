"""Ajouter la table rapport_libelles (libellés éditables des rapports)

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-07-31

Table à clés fixes (pré-remplies ici) permettant à l'admin d'éditer le texte
des champs structurels des rapports tuteur/superviseur (ex: "Y a-t-il des
absences aujourd'hui ?") sans toucher au code de l'app mobile.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "e4f5a6b7c8d9"
down_revision = "d3e4f5a6b7c8"
branch_labels = None
depends_on = None

_DEFAULTS = [
    ("tuteur.semaine_label",           "tuteur",      "Semaine de progression"),
    ("tuteur.absences_question",       "tuteur",      "Y a-t-il des absences aujourd'hui ?"),
    ("tuteur.absents_select_label",    "tuteur",      "Sélectionnez les élèves absents"),
    ("tuteur.difficultes_question",    "tuteur",      "Difficultés rencontrées"),
    ("tuteur.directeur_venu_question", "tuteur",      "Le directeur / superviseur est-il venu ?"),
    ("tuteur.besoin_appui_question",   "tuteur",      "Besoin d'appui pédagogique ?"),
    ("tuteur.domaines_appui_label",    "tuteur",      "Domaines d'appui"),
    ("tuteur.observations_question",   "tuteur",      "Avez-vous des observations ou commentaires ?"),
    ("tuteur.photos_label",            "tuteur",      "Ajoutez des photos de la classe"),
    ("superviseur.classes_terminees_label", "superviseur", "Classes ayant terminé leur planning"),
    ("superviseur.incidents_question",      "superviseur", "Incidents signalés ?"),
    ("superviseur.bilan_label",             "superviseur", "Bilan global"),
    ("superviseur.commentaire_label",       "superviseur", "Commentaire"),
]


def upgrade() -> None:
    op.create_table(
        "rapport_libelles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("cle", sa.String(100), nullable=False, unique=True, index=True),
        sa.Column("cible", sa.String(20), nullable=False),
        sa.Column("texte", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

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
    op.bulk_insert(
        table,
        [
            {
                "id": uuid.uuid4(),
                "cle": cle,
                "cible": cible,
                "texte": texte,
                "created_at": now,
                "updated_at": now,
            }
            for cle, cible, texte in _DEFAULTS
        ],
    )


def downgrade() -> None:
    op.drop_table("rapport_libelles")
