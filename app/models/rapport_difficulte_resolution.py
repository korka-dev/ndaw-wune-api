"""Modèle RapportDifficulteResolution — suivi de résolution par difficulté
signalée dans un rapport journalier (une ligne par (rapport, libellé))."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDMixin


class RapportDifficulteResolution(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "rapport_difficulte_resolutions"
    __table_args__ = (
        UniqueConstraint("rapport_id", "difficulte_label", name="uq_rapport_difficulte_resolution"),
    )

    rapport_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("rapports_journalier.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    difficulte_label: Mapped[str] = mapped_column(String(500), nullable=False)

    resolue:                Mapped[bool]              = mapped_column(Boolean, default=False, nullable=False)
    resolue_par:             Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolue_le:              Mapped[Optional[datetime]]  = mapped_column(DateTime(timezone=True), nullable=True)
    commentaire_resolution:  Mapped[Optional[str]]       = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<RapportDifficulteResolution {self.difficulte_label!r} resolue={self.resolue}>"
