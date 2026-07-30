"""Modèle EleveRemplacement — historique des remplacements d'élève effectués
par un tuteur (avec motif), visible sur le dashboard admin."""
from __future__ import annotations

import uuid
from datetime import date
from typing import Optional

from sqlalchemy import Date, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDMixin


class EleveRemplacement(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "eleve_remplacements"

    ancien_eleve_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("eleves.id", ondelete="SET NULL"), nullable=True
    )
    ancien_eleve_nom: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    nouveau_eleve_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("eleves.id", ondelete="SET NULL"), nullable=True
    )
    nouveau_eleve_nom: Mapped[str] = mapped_column(String(200), nullable=False)

    motif: Mapped[str] = mapped_column(Text, nullable=False)

    teacher_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    school_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="SET NULL"), nullable=True, index=True
    )
    classe: Mapped[str] = mapped_column(String(50), nullable=False)

    date_remplacement: Mapped[date] = mapped_column(Date, server_default=func.current_date(), nullable=False)

    def __repr__(self) -> str:
        return f"<EleveRemplacement {self.ancien_eleve_nom!r} -> {self.nouveau_eleve_nom!r}>"
