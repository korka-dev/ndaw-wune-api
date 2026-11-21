"""Historique des modifications protégé et précis (rapport V2, APP-13)

Revision ID: v0w1x2y3z4a5
Revises: u9v0w1x2y3z4
Create Date: 2026-11-21

- Chaque entrée dit QUI (déjà présent), QUELLE OPÉRATION, SUR QUELLE CIBLE
  (cible_type, cible_id), POURQUOI (motif) et la référence de requête
  (request_id) qui relie l'entrée au journal serveur et au support.
- L'historique est protégé : une entrée ne peut être ni modifiée ni supprimée
  par l'application. Une purge de rétention, décidée par ARED, se fait
  explicitement : SET LOCAL ndawwune.audit_purge = 'on'; DELETE ...
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "v0w1x2y3z4a5"
down_revision = "u9v0w1x2y3z4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("audit_logs", sa.Column("cible_type", sa.String(50), nullable=True))
    op.add_column("audit_logs", sa.Column("cible_id", sa.String(64), nullable=True))
    op.add_column("audit_logs", sa.Column("motif", sa.Text(), nullable=True))
    op.add_column("audit_logs", sa.Column("request_id", sa.String(64), nullable=True))
    op.create_index("ix_audit_logs_cible", "audit_logs", ["cible_type", "cible_id"])
    op.execute("""
        CREATE OR REPLACE FUNCTION audit_logs_proteger() RETURNS trigger AS $$
        BEGIN
            IF coalesce(current_setting('ndawwune.audit_purge', true), '') = 'on' THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION 'historique des modifications protégé (APP-13) : % refusé', TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER audit_logs_protection
        BEFORE UPDATE OR DELETE ON audit_logs
        FOR EACH ROW EXECUTE FUNCTION audit_logs_proteger();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_logs_protection ON audit_logs")
    op.execute("DROP FUNCTION IF EXISTS audit_logs_proteger()")
    op.drop_index("ix_audit_logs_cible", table_name="audit_logs")
    op.drop_column("audit_logs", "request_id")
    op.drop_column("audit_logs", "motif")
    op.drop_column("audit_logs", "cible_id")
    op.drop_column("audit_logs", "cible_type")
