from __future__ import annotations

import uuid
from sqlalchemy import String, Integer, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import mapped_column, Mapped

from app.core.database import Base
from app.models.base import UUIDMixin, TimestampMixin


class Document(UUIDMixin, TimestampMixin, Base):
    """Ressource pédagogique uploadée par un administrateur."""

    __tablename__ = "documents"

    # Titre affiché dans l'interface (saisi par l'admin, ou déduit du nom de fichier)
    title: Mapped[str] = mapped_column(String(255), nullable=False)

    # Nom original du fichier envoyé par le navigateur
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)

    # Nom stocké sur disque (UUID-based pour éviter les collisions)
    stored_filename: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)

    # Type MIME détecté (ex: "application/pdf", "image/png", …)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False, default="application/octet-stream")

    # Taille du fichier en octets
    file_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Description optionnelle
    description: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # Qui a uploadé le fichier
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
