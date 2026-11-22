"""Lot L7 — retours des tuteurs et superviseurs (enquêtes NWV 2026, RET01-RET15)

Revision ID: x2y3z4a5b6c7
Revises: w1x2y3z4a5b6
Create Date: 2026-11-22

Ajoute, sans modifier les données existantes :
- remplacements d'élèves : statut (applique pour l'existant), décision et
  motif de refus (RET03) ;
- rapports journaliers : retour écrit du superviseur (RET05) ;
- documents : activité, niveau, version (RET08) ;
- remarques : destinataire, objet de l'échange, auteur de la réponse (RET13) ;
- contacts_app : dernière synchronisation, version et minuteur transmis (RET15) ;
- propositions_groupe : changements de groupe proposés et décidés (RET07) ;
- evaluation_absences : élève tiré absent le jour de l'évaluation (RET04).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "x2y3z4a5b6c7"
down_revision: Union[str, None] = "w1x2y3z4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)


def _horodatage() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    ]


def upgrade() -> None:
    # RET03 — remplacements
    op.add_column("eleve_remplacements", sa.Column("statut", sa.String(15), nullable=False, server_default="applique"))
    op.add_column("eleve_remplacements", sa.Column("decide_par_id", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True))
    op.add_column("eleve_remplacements", sa.Column("decide_le", sa.DateTime(timezone=True), nullable=True))
    op.add_column("eleve_remplacements", sa.Column("motif_refus", sa.Text(), nullable=True))
    op.create_index("ix_eleve_remplacements_statut", "eleve_remplacements", ["statut"])

    # RET05 — retour du superviseur
    op.add_column("rapports_journalier", sa.Column("retour_superviseur", sa.Text(), nullable=True))
    op.add_column("rapports_journalier", sa.Column("retour_par_id", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True))
    op.add_column("rapports_journalier", sa.Column("retour_le", sa.DateTime(timezone=True), nullable=True))

    # RET08 — classement des ressources
    op.add_column("documents", sa.Column("activite", sa.String(100), nullable=True))
    op.add_column("documents", sa.Column("niveau", sa.String(50), nullable=True))
    op.add_column("documents", sa.Column("version", sa.String(30), nullable=True))

    # RET13 — échanges suivis
    op.add_column("remarques", sa.Column("destinataire", sa.String(20), nullable=False, server_default="equipe_programme"))
    op.add_column("remarques", sa.Column("contexte_type", sa.String(20), nullable=True))
    op.add_column("remarques", sa.Column("contexte_id", sa.String(64), nullable=True))
    op.add_column("remarques", sa.Column("reponse_par_nom", sa.String(150), nullable=True))
    op.create_index("ix_remarques_destinataire", "remarques", ["destinataire"])

    # RET15 — dernière synchronisation connue
    op.create_table(
        "contacts_app",
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("version_app", sa.String(20), nullable=True),
        sa.Column("derniere_sync_le", sa.DateTime(timezone=True), nullable=False),
        sa.Column("minuteur_expose", sa.Boolean(), nullable=True),
        sa.Column("app_access_expose", sa.String(20), nullable=True),
    )

    # RET07 — propositions de changement de groupe
    op.create_table(
        "propositions_groupe",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("eleve_id", UUID, sa.ForeignKey("eleves.id", ondelete="CASCADE"), nullable=False),
        sa.Column("domaine", sa.String(10), nullable=False),
        sa.Column("ancien_groupe", sa.String(30), nullable=True),
        sa.Column("nouveau_groupe", sa.String(30), nullable=False),
        sa.Column("motif", sa.Text(), nullable=False),
        sa.Column("statut", sa.String(15), nullable=False, server_default="proposee"),
        sa.Column("propose_par_id", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("decide_par_id", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("decide_le", sa.DateTime(timezone=True), nullable=True),
        sa.Column("motif_refus", sa.Text(), nullable=True),
        *_horodatage(),
        sa.CheckConstraint("domaine IN ('lecture', 'maths')", name="ck_proposition_groupe_domaine"),
        sa.CheckConstraint("statut IN ('proposee', 'validee', 'refusee')", name="ck_proposition_groupe_statut"),
    )
    op.create_index("ix_propositions_groupe_eleve_id", "propositions_groupe", ["eleve_id"])
    op.create_index("ix_propositions_groupe_statut", "propositions_groupe", ["statut"])
    op.create_index("ix_propositions_groupe_propose_par_id", "propositions_groupe", ["propose_par_id"])

    # RET04 — absences au tirage d'évaluation
    op.create_table(
        "evaluation_absences",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("eleve_id", UUID, sa.ForeignKey("eleves.id", ondelete="CASCADE"), nullable=False),
        sa.Column("superviseur_id", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("session_id", UUID, sa.ForeignKey("program_sessions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("date_eval", sa.Date(), nullable=False),
        *_horodatage(),
        sa.UniqueConstraint("eleve_id", "date_eval", name="uq_evaluation_absence_eleve_date"),
    )
    op.create_index("ix_evaluation_absences_eleve_id", "evaluation_absences", ["eleve_id"])
    op.create_index("ix_evaluation_absences_date_eval", "evaluation_absences", ["date_eval"])


def downgrade() -> None:
    op.drop_table("evaluation_absences")
    op.drop_table("propositions_groupe")
    op.drop_table("contacts_app")
    op.drop_index("ix_remarques_destinataire", table_name="remarques")
    for col in ("reponse_par_nom", "contexte_id", "contexte_type", "destinataire"):
        op.drop_column("remarques", col)
    for col in ("version", "niveau", "activite"):
        op.drop_column("documents", col)
    for col in ("retour_le", "retour_par_id", "retour_superviseur"):
        op.drop_column("rapports_journalier", col)
    op.drop_index("ix_eleve_remplacements_statut", table_name="eleve_remplacements")
    for col in ("motif_refus", "decide_le", "decide_par_id", "statut"):
        op.drop_column("eleve_remplacements", col)
