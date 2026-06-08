"""Ajouter la table evaluations_eleves

Revision ID: f7e8d9c0b1a2
Revises: b22e647b0faf
Create Date: 2026-06-06

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "f7e8d9c0b1a2"
down_revision = "b22e647b0faf"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evaluations_eleves",
        sa.Column("id",             UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("created_at",     sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at",     sa.DateTime(timezone=True), nullable=False),
        sa.Column("superviseur_id", UUID(as_uuid=True), sa.ForeignKey("users.id",           ondelete="CASCADE"),  nullable=False, index=True),
        sa.Column("eleve_id",       UUID(as_uuid=True), sa.ForeignKey("eleves.id",           ondelete="CASCADE"),  nullable=False, index=True),
        sa.Column("session_id",     UUID(as_uuid=True), sa.ForeignKey("program_sessions.id", ondelete="SET NULL"), nullable=True,  index=True),
        sa.Column("competence",     sa.String(100), nullable=False),
        sa.Column("resultat",       sa.String(20),  nullable=False),
        sa.Column("date_eval",      sa.Date,        nullable=False, index=True),
        sa.Column("commentaire",    sa.String(500), nullable=True),
        sa.UniqueConstraint(
            "superviseur_id", "eleve_id", "competence", "date_eval",
            name="uq_eval_superviseur_eleve_competence_date",
        ),
    )


def downgrade() -> None:
    op.drop_table("evaluations_eleves")
