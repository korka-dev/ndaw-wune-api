"""remarques + usage_logs + semaine planning/presences + app_access + present tirage

Revision ID: k4l5m6n7o8p9
Revises: j3k4l5m6n7o8
Create Date: 2026-07-13
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "k4l5m6n7o8p9"
down_revision = "j3k4l5m6n7o8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Planning : semaine ────────────────────────────────────────────────────
    op.add_column("planning_segments", sa.Column("semaine", sa.Integer(), nullable=True))
    op.create_index("ix_planning_segments_semaine", "planning_segments", ["semaine"])
    op.drop_constraint("uq_planning_segment", "planning_segments", type_="unique")
    op.create_unique_constraint(
        "uq_planning_segment", "planning_segments",
        ["session_id", "semaine", "jour", "heure_debut"],
    )

    # ── Users : app_access ────────────────────────────────────────────────────
    op.add_column(
        "users",
        sa.Column("app_access", sa.String(20), nullable=False, server_default="full"),
    )

    # ── Évaluation : présence de l'élève tiré ─────────────────────────────────
    op.add_column("evaluation_tirages", sa.Column("present", sa.Boolean(), nullable=True))

    # ── Pointage superviseur : période ────────────────────────────────────────
    op.add_column("supervisor_presence_checks", sa.Column("semaine",    sa.Integer(), nullable=True))
    op.add_column("supervisor_presence_checks", sa.Column("jour_cours", sa.Integer(), nullable=True))

    # ── Logs d'utilisation ────────────────────────────────────────────────────
    op.create_table(
        "usage_logs",
        sa.Column("id",         UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id",    UUID(as_uuid=True), nullable=True),
        sa.Column("user_name",  sa.String(150), nullable=False),
        sa.Column("user_role",  sa.String(30),  nullable=False),
        sa.Column("feature",    sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_usage_logs_user_id", "usage_logs", ["user_id"])
    op.create_index("ix_usage_logs_feature", "usage_logs", ["feature"])
    op.create_index("ix_usage_logs_created_at", "usage_logs", ["created_at"])

    # ── Remarques ─────────────────────────────────────────────────────────────
    op.create_table(
        "remarques",
        sa.Column("id",         UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id",    UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("user_name",  sa.String(150), nullable=False),
        sa.Column("user_role",  sa.String(30),  nullable=False),
        sa.Column("ecole",      sa.String(255), nullable=True),
        sa.Column("categorie",  sa.String(50),  nullable=False),
        sa.Column("message",    sa.Text(),      nullable=False),
        sa.Column("statut",     sa.String(20),  nullable=False, server_default="nouveau"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_remarques_user_id",   "remarques", ["user_id"])
    op.create_index("ix_remarques_categorie", "remarques", ["categorie"])
    op.create_index("ix_remarques_statut",    "remarques", ["statut"])


def downgrade() -> None:
    op.drop_table("remarques")
    op.drop_table("usage_logs")
    op.drop_column("supervisor_presence_checks", "jour_cours")
    op.drop_column("supervisor_presence_checks", "semaine")
    op.drop_column("evaluation_tirages", "present")
    op.drop_column("users", "app_access")
    op.drop_constraint("uq_planning_segment", "planning_segments", type_="unique")
    op.create_unique_constraint(
        "uq_planning_segment", "planning_segments",
        ["session_id", "jour", "heure_debut"],
    )
    op.drop_index("ix_planning_segments_semaine", table_name="planning_segments")
    op.drop_column("planning_segments", "semaine")
