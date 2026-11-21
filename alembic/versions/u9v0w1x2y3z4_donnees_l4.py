"""Exploitation des données (rapport V2, lot L4 — Partie II)

Revision ID: u9v0w1x2y3z4
Revises: t8u9v0w1x2y3
Create Date: 2026-11-21

- D4.2 / D5 : rapports journaliers rattachés à l'école et au superviseur
  connus à la réception (NULL pour l'historique : aucun rattachement deviné).
- D3.3 : identifiants des élèves absents (table individuelle d'absences).
- D5.1 : effectif attendu (inscrits éligibles) conservé comme référence.
- B1 : horodatage de saisie côté client (submitted_at), distinct de la
  réception serveur (created_at).
- D5.2 / REC13 : versions des questions dynamiques ; la version 1 de chaque
  question existante reprend sa définition actuelle.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "u9v0w1x2y3z4"
down_revision = "t8u9v0w1x2y3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("rapports_journalier", sa.Column("ecole_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("rapports_journalier", sa.Column("superviseur_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("rapports_journalier", sa.Column("absents_ids", postgresql.JSONB(), nullable=True))
    op.add_column("rapports_journalier", sa.Column("effectif_attendu", sa.Integer(), nullable=True))
    op.add_column("rapports_journalier", sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "rapports_journalier_ecole_id_fkey", "rapports_journalier", "schools",
        ["ecole_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "rapports_journalier_superviseur_id_fkey", "rapports_journalier", "users",
        ["superviseur_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_index("ix_rapports_journalier_ecole_id", "rapports_journalier", ["ecole_id"])
    op.create_index("ix_rapports_journalier_superviseur_id", "rapports_journalier", ["superviseur_id"])

    op.add_column(
        "rapport_questions",
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
    )
    op.create_table(
        "rapport_question_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("question_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(500), nullable=False),
        sa.Column("type", sa.String(30), nullable=False),
        sa.Column("options", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("cible", sa.String(20), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("question_id", "version", name="uq_rapport_question_version"),
    )
    op.create_index("ix_rapport_question_versions_question_id", "rapport_question_versions", ["question_id"])
    # Version 1 = définition actuelle, valable depuis la création de la question.
    op.execute(
        """
        INSERT INTO rapport_question_versions (id, question_id, version, label, type, options, cible, valid_from)
        SELECT gen_random_uuid(), id, 1, label, type, options, cible, created_at FROM rapport_questions
        """
    )


def downgrade() -> None:
    op.drop_index("ix_rapport_question_versions_question_id", table_name="rapport_question_versions")
    op.drop_table("rapport_question_versions")
    op.drop_column("rapport_questions", "version")

    op.drop_index("ix_rapports_journalier_superviseur_id", table_name="rapports_journalier")
    op.drop_index("ix_rapports_journalier_ecole_id", table_name="rapports_journalier")
    op.drop_constraint("rapports_journalier_superviseur_id_fkey", "rapports_journalier", type_="foreignkey")
    op.drop_constraint("rapports_journalier_ecole_id_fkey", "rapports_journalier", type_="foreignkey")
    for col in ("submitted_at", "effectif_attendu", "absents_ids", "superviseur_id", "ecole_id"):
        op.drop_column("rapports_journalier", col)
