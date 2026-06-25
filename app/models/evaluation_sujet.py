"""Modèle EvaluationSujet — sujet d'évaluation créé par l'admin."""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDMixin


class EvaluationSujet(Base, UUIDMixin, TimestampMixin):
    """
    Sujet d'évaluation défini par l'admin.
    Déclenche un tirage aléatoire d'élèves au moment de la création.
    """
    __tablename__ = "evaluation_sujets"

    titre: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Nombre d'élèves à tirer par classe (0 = tous)
    nb_eleves_par_classe: Mapped[int] = mapped_column(Integer, nullable=False, default=5)

    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("program_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Relations
    tirages: Mapped[list["EvaluationTirage"]] = relationship(
        "EvaluationTirage", back_populates="sujet", cascade="all, delete-orphan"
    )
    creator: Mapped[Optional["User"]] = relationship("User", foreign_keys=[created_by])
    session: Mapped[Optional["ProgramSession"]] = relationship("ProgramSession", foreign_keys=[session_id])

    def __repr__(self) -> str:
        return f"<EvaluationSujet {self.titre!r}>"
