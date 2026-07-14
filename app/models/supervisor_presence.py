"""Modèle SupervisorPresenceCheck — pointage quotidien des enseignants par un superviseur."""
from __future__ import annotations

import uuid
from datetime import date
from typing import Optional

from sqlalchemy import Boolean, Date, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDMixin


class SupervisorPresenceCheck(Base, UUIDMixin, TimestampMixin):
    """
    Enregistre le pointage de présence d'un enseignant, réalisé par son
    superviseur de terrain pour une journée donnée.

    - present : True = présent, False = absent
    - motif   : raison de l'absence (uniquement si present = False)

    Un superviseur ne peut pointer le même enseignant qu'une fois par jour
    (re-soumettre met simplement à jour le pointage existant).
    """

    __tablename__ = "supervisor_presence_checks"
    __table_args__ = (
        UniqueConstraint(
            "superviseur_id", "teacher_id", "date_jour",
            name="uq_sup_presence_superviseur_teacher_date",
        ),
    )

    superviseur_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    teacher_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    date_jour: Mapped[date]            = mapped_column(Date, nullable=False, index=True)
    # Période du programme (choisie par le superviseur avant le pointage)
    semaine:    Mapped[Optional[int]]  = mapped_column(Integer, nullable=True)
    jour_cours: Mapped[Optional[int]]  = mapped_column(Integer, nullable=True)
    present:   Mapped[bool]            = mapped_column(Boolean, nullable=False)
    motif:     Mapped[Optional[str]]   = mapped_column(String(200), nullable=True)

    # Relations (lecture seule)
    superviseur: Mapped["User"] = relationship("User", foreign_keys=[superviseur_id])
    teacher:     Mapped["User"] = relationship("User", foreign_keys=[teacher_id])

    def __repr__(self) -> str:
        return f"<SupervisorPresenceCheck sup={self.superviseur_id} teacher={self.teacher_id} {self.date_jour} present={self.present}>"
