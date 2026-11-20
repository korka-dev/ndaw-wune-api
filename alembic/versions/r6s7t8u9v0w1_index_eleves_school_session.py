"""Index eleves.school_id et eleves.session_id (alignement modèles / base, APP-27)

Revision ID: r6s7t8u9v0w1
Revises: q5r6s7t8u9v0
Create Date: 2026-11-20

Les modèles déclarent ces index (index=True) mais aucune migration ne les
créait : `alembic check` signalait l'écart. IF NOT EXISTS : sans effet si un
index du même nom a été créé à la main sur un serveur. Aucune donnée modifiée.
"""
from __future__ import annotations

from alembic import op

revision = "r6s7t8u9v0w1"
down_revision = "q5r6s7t8u9v0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS ix_eleves_school_id ON eleves (school_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_eleves_session_id ON eleves (session_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_eleves_session_id")
    op.execute("DROP INDEX IF EXISTS ix_eleves_school_id")
