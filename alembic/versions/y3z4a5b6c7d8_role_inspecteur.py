"""Lot L8 — rôle inspecteur (IEF) en consultation sur l'application mobile

Revision ID: y3z4a5b6c7d8
Revises: x2y3z4a5b6c7
Create Date: 2026-11-24

- valeur 'inspecteur' ajoutée au type user_role (dans son propre bloc
  autocommit : une valeur ENUM ajoutée n'est utilisable qu'après commit) ;
- colonne users.ief : IEF consultée par l'inspecteur, même libellé que
  schools.region. Aucune donnée existante n'est modifiée.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "y3z4a5b6c7d8"
down_revision: Union[str, None] = "x2y3z4a5b6c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(sa.text("ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'inspecteur'"))
    op.add_column("users", sa.Column("ief", sa.String(200), nullable=True))


def downgrade() -> None:
    # Refusé tant qu'un compte inspecteur existe : on ne supprime pas de compte
    # (ni son historique) pour pouvoir redescendre de version.
    op.execute(sa.text("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM users WHERE role::text = 'inspecteur') THEN
            RAISE EXCEPTION 'Des comptes inspecteur existent : les archiver ou changer leur rôle avant de redescendre.';
          END IF;
        END $$;
    """))
    op.drop_column("users", "ief")
    op.execute(sa.text("ALTER TYPE user_role RENAME TO user_role_old"))
    op.execute(sa.text("CREATE TYPE user_role AS ENUM ('admin', 'coordonnateur', 'enseignant', 'superviseur')"))
    op.execute(sa.text("ALTER TABLE users ALTER COLUMN role TYPE user_role USING role::text::user_role"))
    op.execute(sa.text("DROP TYPE user_role_old"))
