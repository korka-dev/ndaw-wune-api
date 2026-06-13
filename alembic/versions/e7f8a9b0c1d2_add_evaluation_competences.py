"""Ajouter la table evaluation_competences (compétences d'évaluation configurables)

Revision ID: e7f8a9b0c1d2
Revises: d1e2f3a4b5c6
Create Date: 2026-06-13
"""
from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e7f8a9b0c1d2"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


SEED_COMPETENCES = [
    ("Lecture · Sons des lettres",        "lettres",    0),
    ("Lecture · Syllabes",                "syllabes",   1),
    ("Lecture · Mots",                    "mots",       2),
    ("Mathématiques · Opérations",        "operations", 3),
]


def upgrade() -> None:
    op.create_table(
        "evaluation_competences",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("code", sa.String(50), nullable=False, unique=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("ordre", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    table = sa.table(
        "evaluation_competences",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("label", sa.String),
        sa.column("code", sa.String),
        sa.column("active", sa.Boolean),
        sa.column("ordre", sa.Integer),
    )
    op.bulk_insert(
        table,
        [
            {"id": uuid.uuid4(), "label": label, "code": code, "active": True, "ordre": ordre}
            for label, code, ordre in SEED_COMPETENCES
        ],
    )


def downgrade() -> None:
    op.drop_table("evaluation_competences")
