"""Modèle ProgressionConfig — nombre de semaines/jours du programme,
configurable par école et/ou par session (remplace les constantes codées en
dur 25 semaines / jours côté app mobile)."""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import ForeignKey, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDMixin

DEFAULT_NB_SEMAINES = 10
DEFAULT_NB_JOURS = 3


class ProgressionConfig(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "progression_configs"
    __table_args__ = (
        UniqueConstraint("school_id", "session_id", name="uq_progression_config_school_session"),
    )

    school_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=True, index=True
    )
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("program_sessions.id", ondelete="CASCADE"), nullable=True, index=True
    )

    nb_semaines: Mapped[int] = mapped_column(Integer, default=DEFAULT_NB_SEMAINES, server_default=str(DEFAULT_NB_SEMAINES), nullable=False)
    nb_jours:    Mapped[int] = mapped_column(Integer, default=DEFAULT_NB_JOURS,    server_default=str(DEFAULT_NB_JOURS),    nullable=False)

    def __repr__(self) -> str:
        return f"<ProgressionConfig school={self.school_id} session={self.session_id} {self.nb_semaines}sem/{self.nb_jours}j>"
