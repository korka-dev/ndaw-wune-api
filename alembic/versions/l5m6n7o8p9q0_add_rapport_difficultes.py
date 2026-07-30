"""Ajouter la table rapport_difficultes

Revision ID: l5m6n7o8p9q0
Revises: k4l5m6n7o8p9
Create Date: 2026-07-26

Permet à l'admin de configurer la liste des difficultés proposées dans
l'étape « Difficultés » du formulaire de rapport journalier mobile
(remplace la liste codée en dur DIFFICULTES_LIST côté app).
"""
from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "l5m6n7o8p9q0"
down_revision = "k4l5m6n7o8p9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rapport_difficultes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("label", sa.String(500), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("ordre", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    defaults = [
        "Gestion des groupes",
        "Gestion du temps",
        "Enseignement de la leçon de lecture",
        "Enseignement de la leçon de mathématiques",
        "Evaluation des apprentissages",
        "Aide aux élèves en difficulté",
        "Utilisation du guide",
        "Promesse Ndaw Wune",
        "Organisation de la classe",
        "Chanson de l'alphabet",
        "Utilisation du récit",
        "Jeux éducatifs",
        "Le respect de la démarche du guide",
        "Autres",
        "Aucune",
    ]
    table = sa.table(
        "rapport_difficultes",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("label", sa.String),
        sa.column("active", sa.Boolean),
        sa.column("ordre", sa.Integer),
    )
    op.bulk_insert(
        table,
        [
            {"id": uuid.uuid4(), "label": label, "active": True, "ordre": i}
            for i, label in enumerate(defaults)
        ],
    )


def downgrade() -> None:
    op.drop_table("rapport_difficultes")
