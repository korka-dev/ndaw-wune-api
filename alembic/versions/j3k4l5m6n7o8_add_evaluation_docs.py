"""add evaluation_docs table

Revision ID: j3k4l5m6n7o8
Revises: i2j3k4l5m6n7
Create Date: 2026-06-28
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "j3k4l5m6n7o8"
down_revision = "i2j3k4l5m6n7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evaluation_docs",
        sa.Column("id",         UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("langue",     sa.String(100),  nullable=False),
        sa.Column("titre",      sa.String(255),  nullable=False),
        sa.Column("lettres",    JSONB,           nullable=False, server_default="[]"),
        sa.Column("syllabes",   JSONB,           nullable=False, server_default="[]"),
        sa.Column("mots",       JSONB,           nullable=False, server_default="[]"),
        sa.Column("operations", JSONB,           nullable=False, server_default="[]"),
        sa.Column("is_active",  sa.Boolean(),    nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_evaluation_docs_langue", "evaluation_docs", ["langue"])
    op.create_index("ix_evaluation_docs_is_active", "evaluation_docs", ["is_active"])

    # Données initiales : 3 dossiers existants
    op.execute("""
        INSERT INTO evaluation_docs (id, langue, titre, lettres, syllabes, mots, operations, is_active, created_at, updated_at)
        VALUES
        (
            gen_random_uuid(),
            'Seereer',
            'Test Élève en Seereer',
            '["a","l","t","e","n","r","m","k","g","s"]',
            '["wo","si","ka","ko","ta","am","fi","nu","at","de"]',
            '["met","tali","kalaas","yaru","bat","laamit","fuuli","simin","fog","mayu"]',
            '["22 + 35 =","34 + 12 =","19 - 7 =","45 - 33 ="]',
            true,
            now(), now()
        ),
        (
            gen_random_uuid(),
            'Pulaar',
            'Test Élève en Pulaar',
            '["a","l","t","e","n","r","m","k","g","s"]',
            '["as","yo","kii","ko","ta","am","fi","nii","to","de"]',
            '["bee","makko","galle","lekkol","maama","kadi","tawii","woni","maa","goggo"]',
            '["22 + 35 =","34 + 12 =","19 - 7 =","45 - 33 ="]',
            true,
            now(), now()
        ),
        (
            gen_random_uuid(),
            'Wolof',
            'Test Élève en Wolof',
            '["a","l","t","e","n","r","m","k","g","s"]',
            '["gi","bi","ak","ko","di","am","la","nu","ay","de"]',
            '["meew","tali","kalaas","kàddu","baat","liggeey","tuuti","garab","bokk","dafay"]',
            '["22 + 35 =","34 + 12 =","19 - 7 =","45 - 33 ="]',
            true,
            now(), now()
        );
    """)


def downgrade() -> None:
    op.drop_index("ix_evaluation_docs_is_active", table_name="evaluation_docs")
    op.drop_index("ix_evaluation_docs_langue", table_name="evaluation_docs")
    op.drop_table("evaluation_docs")
