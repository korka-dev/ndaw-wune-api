"""Intégrité et historique (rapport V2, lot L3)

Revision ID: t8u9v0w1x2y3
Revises: s7t8u9v0w1x2
Create Date: 2026-11-20

- APP-15 : archivage (archived_at) des écoles, classes, élèves, comptes et
  campagnes ; les observations ne sont plus supprimées en cascade (FK RESTRICT).
- APP-22 : session_id sur rapports journaliers et pointages (NULL pour
  l'historique : aucun rattachement deviné).
- APP-30 : historique des résultats d'évaluation corrigés (audios conservés).
- APP-31 : statut / version / motif des rapports journaliers + versions.
- APP-35 : e-mails normalisés (minuscules, sans espaces) et unicité insensible
  à la casse. Les COLLISIONS existantes ne sont jamais fusionnées : la
  migration s'arrête et les liste (voir scripts/collisions_identifiants.py).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "t8u9v0w1x2y3"
down_revision = "s7t8u9v0w1x2"
branch_labels = None
depends_on = None

# (table, colonne, table cible) : observations protégées contre la suppression.
FK_OBSERVATIONS = [
    ("seances", "session_id", "program_sessions"),
    ("seances", "teacher_id", "users"),
    ("rapports_prof", "seance_id", "seances"),
    ("rapports_prof", "teacher_id", "users"),
    ("rapports_journalier", "teacher_id", "users"),
    ("eleves", "school_id", "schools"),
    ("evaluations_eleves", "superviseur_id", "users"),
    ("evaluations_eleves", "eleve_id", "eleves"),
    ("supervisor_presence_checks", "superviseur_id", "users"),
    ("supervisor_presence_checks", "teacher_id", "users"),
    ("evaluation_tirages", "sujet_id", "evaluation_sujets"),
    ("evaluation_tirages", "eleve_id", "eleves"),
    ("evaluation_resultats", "tirage_id", "evaluation_tirages"),
    ("eleve_remplacements", "teacher_id", "users"),
    ("rapport_difficulte_resolutions", "rapport_id", "rapports_journalier"),
]

ARCHIVABLES = ["schools", "school_classes", "eleves", "users", "program_sessions"]


def _set_fk(ondelete: str) -> None:
    for table, col, cible in FK_OBSERVATIONS:
        name = f"{table}_{col}_fkey"
        op.drop_constraint(name, table, type_="foreignkey")
        op.create_foreign_key(name, table, cible, [col], ["id"], ondelete=ondelete)


def upgrade() -> None:
    conn = op.get_bind()

    # ── APP-35 : collisions d'e-mails (arbitrage humain, jamais de fusion) ──
    collisions = conn.execute(sa.text(
        "SELECT lower(btrim(email)) AS e, string_agg(id::text || ' (' || name || ')', ', ') "
        "FROM users WHERE email IS NOT NULL AND btrim(email) <> '' "
        "GROUP BY lower(btrim(email)) HAVING count(*) > 1"
    )).all()
    if collisions:
        detail = "\n".join(f"  {e} : {ids}" for e, ids in collisions)
        raise RuntimeError(
            "APP-35 : comptes dont l'e-mail ne diffère que par la casse ou des espaces. "
            "Arbitrez-les (modifier ou vider l'e-mail du compte à écarter) puis relancez la migration :\n"
            + detail
        )
    conn.execute(sa.text("UPDATE users SET email = NULL WHERE email IS NOT NULL AND btrim(email) = ''"))
    conn.execute(sa.text("UPDATE users SET email = lower(btrim(email)) WHERE email <> lower(btrim(email))"))
    op.create_index("uq_users_email_lower", "users", [sa.text("lower(email)")], unique=True)

    # ── APP-15 : archivage + FK RESTRICT ────────────────────────────────────
    for t in ARCHIVABLES:
        op.add_column(t, sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
        op.create_index(f"ix_{t}_archived_at", t, ["archived_at"])
    _set_fk("RESTRICT")

    # ── APP-22 : campagne des événements ────────────────────────────────────
    for t in ("rapports_journalier", "supervisor_presence_checks"):
        op.add_column(t, sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True))
        op.create_foreign_key(f"{t}_session_id_fkey", t, "program_sessions", ["session_id"], ["id"], ondelete="RESTRICT")
        op.create_index(f"ix_{t}_session_id", t, ["session_id"])

    # ── APP-31 : cycle de vie des rapports journaliers ──────────────────────
    op.add_column("rapports_journalier", sa.Column("statut", sa.String(20), nullable=False, server_default="soumis"))
    op.add_column("rapports_journalier", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("rapports_journalier", sa.Column("statut_motif", sa.Text(), nullable=True))
    op.add_column("rapports_journalier", sa.Column("statut_par_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("rapports_journalier", sa.Column("statut_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key("rapports_journalier_statut_par_id_fkey", "rapports_journalier", "users",
                          ["statut_par_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_rapports_journalier_statut", "rapports_journalier", ["statut"])
    op.create_table(
        "rapport_journalier_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("rapport_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("rapports_journalier.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("statut", sa.String(20), nullable=False),
        sa.Column("contenu", postgresql.JSONB(), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("motif", sa.Text(), nullable=False),
        sa.Column("auteur_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("rapport_id", "version", name="uq_rapport_journalier_version"),
    )
    op.create_index("ix_rapport_journalier_versions_rapport_id", "rapport_journalier_versions", ["rapport_id"])

    # ── APP-30 : historique des résultats corrigés ──────────────────────────
    op.create_table(
        "evaluation_resultat_historique",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("resultat_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("evaluation_resultats.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("tirage_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("evaluation_tirages.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("superviseur_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("resultat", sa.String(30), nullable=False),
        sa.Column("commentaire", sa.Text(), nullable=True),
        sa.Column("audio_filename", sa.String(255), nullable=True),
        sa.Column("observe_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("remplace_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("remplace_par_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )
    for c in ("resultat_id", "tirage_id", "audio_filename"):
        op.create_index(f"ix_evaluation_resultat_historique_{c}", "evaluation_resultat_historique", [c])


def downgrade() -> None:
    op.drop_table("evaluation_resultat_historique")
    op.drop_table("rapport_journalier_versions")
    op.drop_index("ix_rapports_journalier_statut", table_name="rapports_journalier")
    op.drop_constraint("rapports_journalier_statut_par_id_fkey", "rapports_journalier", type_="foreignkey")
    for c in ("statut_at", "statut_par_id", "statut_motif", "version", "statut"):
        op.drop_column("rapports_journalier", c)
    for t in ("rapports_journalier", "supervisor_presence_checks"):
        op.drop_index(f"ix_{t}_session_id", table_name=t)
        op.drop_constraint(f"{t}_session_id_fkey", t, type_="foreignkey")
        op.drop_column(t, "session_id")
    _set_fk("CASCADE")
    for t in ARCHIVABLES:
        op.drop_index(f"ix_{t}_archived_at", table_name=t)
        op.drop_column(t, "archived_at")
    op.drop_index("uq_users_email_lower", table_name="users")
    # Les e-mails restent normalisés (minuscules) : sans effet sur la connexion,
    # qui compare déjà en minuscules.
