"""add code_ecole and code_eleve

Revision ID: i2j3k4l5m6n7
Revises: h1i2j3k4l5m6
Create Date: 2026-06-25
"""
from alembic import op
import sqlalchemy as sa

revision = "i2j3k4l5m6n7"
down_revision = "h1i2j3k4l5m6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("schools", sa.Column("code_ecole", sa.Integer(), nullable=True))
    op.create_unique_constraint("uq_schools_code_ecole", "schools", ["code_ecole"])
    op.create_index("ix_schools_code_ecole", "schools", ["code_ecole"])

    op.add_column("eleves", sa.Column("code_eleve", sa.String(30), nullable=True))
    op.create_index("ix_eleves_code_eleve", "eleves", ["code_eleve"])


def downgrade() -> None:
    op.drop_index("ix_eleves_code_eleve", table_name="eleves")
    op.drop_column("eleves", "code_eleve")

    op.drop_index("ix_schools_code_ecole", table_name="schools")
    op.drop_constraint("uq_schools_code_ecole", "schools", type_="unique")
    op.drop_column("schools", "code_ecole")
