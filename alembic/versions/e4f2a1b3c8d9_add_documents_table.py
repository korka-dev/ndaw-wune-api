"""add documents table

Revision ID: e4f2a1b3c8d9
Revises: b3c4d5e6f7a8
Create Date: 2026-05-21 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e4f2a1b3c8d9"
down_revision: Union[str, None] = "b3c4d5e6f7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id",                postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("title",             sa.String(255), nullable=False),
        sa.Column("original_filename", sa.String(512), nullable=False),
        sa.Column("stored_filename",   sa.String(512), nullable=False, unique=True),
        sa.Column("mime_type",         sa.String(128), nullable=False, server_default="application/octet-stream"),
        sa.Column("file_size",         sa.Integer,     nullable=False, server_default="0"),
        sa.Column("description",       sa.String(1024), nullable=True),
        sa.Column(
            "uploaded_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_documents_created_at", "documents", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_documents_created_at", table_name="documents")
    op.drop_table("documents")
