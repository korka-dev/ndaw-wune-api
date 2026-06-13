"""Ajouter la table rapport_questions et le champ reponses_questions

Revision ID: d1e2f3a4b5c6
Revises: a56d5527746c
Create Date: 2026-06-13

Permet à l'admin de configurer dynamiquement des questions complémentaires
affichées dans le formulaire de rapport journalier mobile. Les réponses sont
stockées en JSON dans `rapports_journalier.reponses_questions`.
"""
from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d1e2f3a4b5c6"
down_revision = "a56d5527746c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rapport_questions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("label", sa.String(500), nullable=False),
        sa.Column("type", sa.String(30), nullable=False),
        sa.Column("options", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("ordre", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.add_column(
        "rapports_journalier",
        sa.Column("reponses_questions", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("rapports_journalier", "reponses_questions")
    op.drop_table("rapport_questions")
