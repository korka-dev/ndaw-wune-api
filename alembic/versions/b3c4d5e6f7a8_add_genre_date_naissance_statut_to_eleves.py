"""create eleves table with genre, date_naissance, statut

Revision ID: b3c4d5e6f7a8
Revises: a1b2c3d4e5f6
Create Date: 2026-05-20 00:00:00.000000

"""
from __future__ import annotations

from alembic import op

revision = "b3c4d5e6f7a8"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Crée la table si elle n'existe pas encore (première migration pour eleves)
    op.execute("""
        CREATE TABLE IF NOT EXISTS eleves (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            nom         VARCHAR(100) NOT NULL,
            prenom      VARCHAR(100),
            classe      VARCHAR(50)  NOT NULL,
            genre       VARCHAR(20),
            date_naissance VARCHAR(10),
            statut      VARCHAR(20)  NOT NULL DEFAULT 'actif',
            school_id   UUID REFERENCES schools(id)           ON DELETE CASCADE,
            session_id  UUID REFERENCES program_sessions(id)  ON DELETE SET NULL,
            created_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            updated_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            CONSTRAINT uq_eleve_school_classe_nom UNIQUE (school_id, classe, nom, prenom)
        )
    """)
    # Si la table existait déjà (créée manuellement), on s'assure que les nouvelles colonnes existent
    op.execute("ALTER TABLE eleves ADD COLUMN IF NOT EXISTS genre          VARCHAR(20)")
    op.execute("ALTER TABLE eleves ADD COLUMN IF NOT EXISTS date_naissance VARCHAR(10)")
    op.execute("ALTER TABLE eleves ADD COLUMN IF NOT EXISTS statut         VARCHAR(20) NOT NULL DEFAULT 'actif'")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS eleves")
