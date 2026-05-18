"""Créer la table rapports_journalier (photo_classe_url en Text)

Revision ID: f3a1c8e2b945
Revises: d2bfed0fb6d1
Create Date: 2026-05-18

La table n'était dans aucune migration précédente.
Si elle existe déjà (migration manuelle ou create_all), on s'assure juste
que photo_classe_url est bien de type TEXT.
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from alembic import op
from sqlalchemy import inspect

revision = "f3a1c8e2b945"
down_revision = "d2bfed0fb6d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    exists = inspect(conn).has_table("rapports_journalier")

    if not exists:
        # ── Créer la table from scratch ───────────────────────────────────
        op.create_table(
            "rapports_journalier",
            sa.Column("id",                       UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("teacher_id",               UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),

            # Données administratives
            sa.Column("date_rapport",             sa.Date(),          nullable=False),
            sa.Column("ief",                      sa.String(200),     nullable=False),
            sa.Column("commune",                  sa.String(200),     nullable=False),
            sa.Column("ecole",                    sa.String(255),     nullable=False),
            sa.Column("superviseur",              sa.String(255),     nullable=False),
            sa.Column("nom_tuteur",               sa.String(255),     nullable=False),

            # Présences
            sa.Column("nb_absences",              sa.Integer(),       nullable=False, server_default="0"),
            sa.Column("absents",                  sa.Text(),          nullable=True),
            sa.Column("semaine",                  sa.Integer(),       nullable=False),
            sa.Column("jour_cours",               sa.Integer(),       nullable=False),

            # Difficultés
            sa.Column("difficultes",              sa.Text(),          nullable=False),
            sa.Column("autres_difficultes",       sa.Text(),          nullable=True),
            sa.Column("description_difficultes",  sa.Text(),          nullable=True),

            # Supervision
            sa.Column("directeur_venu",           sa.Boolean(),       nullable=False),
            sa.Column("besoin_appui",             sa.Boolean(),       nullable=False),
            sa.Column("domaines_appui",           sa.Text(),          nullable=True),
            sa.Column("has_observations",         sa.Boolean(),       nullable=False, server_default="false"),
            sa.Column("commentaires",             sa.Text(),          nullable=True),

            # Métadonnées
            sa.Column("soumis_en_offline",        sa.Boolean(),       nullable=False, server_default="true"),
            sa.Column("photo_classe_url",         sa.Text(),          nullable=True),

            # Timestamps
            sa.Column("created_at",               sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at",               sa.DateTime(timezone=True), nullable=False),
        )
    else:
        # ── Table déjà présente : s'assurer que photo_classe_url est TEXT ─
        col_type = {
            c["name"]: c["type"]
            for c in inspect(conn).get_columns("rapports_journalier")
        }.get("photo_classe_url")

        if col_type is not None and not isinstance(col_type, sa.Text):
            op.alter_column(
                "rapports_journalier",
                "photo_classe_url",
                type_=sa.Text(),
                existing_nullable=True,
            )


def downgrade() -> None:
    conn = op.get_bind()
    if inspect(conn).has_table("rapports_journalier"):
        # On ne supprime pas la table en downgrade (risque de perte de données)
        # On remet juste la contrainte de longueur si besoin
        op.alter_column(
            "rapports_journalier",
            "photo_classe_url",
            type_=sa.String(500),
            existing_nullable=True,
        )
