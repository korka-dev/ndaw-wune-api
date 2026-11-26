import uuid
from datetime import time
from typing import Optional
from sqlalchemy import Boolean, CheckConstraint, String, Integer, Time, ForeignKey, UniqueConstraint, false
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from app.core.database import Base
from app.models.base import UUIDMixin, TimestampMixin


class PlanningSegment(Base, UUIDMixin, TimestampMixin):
    """Créneau planifié : session, jour, heure, activité."""
    __tablename__ = "planning_segments"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("program_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    teacher_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    school_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("schools.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Semaine du programme concernée (1, 2, 3…) — None = toutes les semaines
    semaine:     Mapped[Optional[int]]  = mapped_column(Integer, nullable=True, index=True)

    jour:        Mapped[int]            = mapped_column(Integer, nullable=False)
    heure_debut: Mapped[time]           = mapped_column(Time, nullable=False)
    heure_fin:   Mapped[time]           = mapped_column(Time, nullable=False)
    classe:      Mapped[Optional[str]]  = mapped_column(String(50), nullable=True)
    matiere:     Mapped[Optional[str]]  = mapped_column(String(100), nullable=True)
    # Lot L9 : créneau réservé aux écoles avec FLS (français langue seconde),
    # masqué aux tuteurs des écoles sans FLS.
    reserve_fls: Mapped[bool]           = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    # Lot L11 : emploi du temps du type d'école — « tous », « fls » (écoles
    # avec FLS) ou « sans_fls ». `reserve_fls` reste égal à (type_ecole == "fls").
    type_ecole:  Mapped[str]            = mapped_column(String(10), nullable=False, default="tous", server_default="tous")

    __table_args__ = (
        UniqueConstraint("session_id", "semaine", "jour", "heure_debut", "type_ecole", name="uq_planning_segment"),
        CheckConstraint("type_ecole IN ('tous', 'fls', 'sans_fls')", name="ck_planning_type_ecole"),
    )

    session: Mapped["ProgramSession"] = relationship("ProgramSession", back_populates="planning_segments")
    teacher: Mapped["User"]           = relationship("User")
    school:  Mapped[Optional["School"]] = relationship("School")

    def __repr__(self):
        return f"<PlanningSegment jour={self.jour} {self.heure_debut}-{self.heure_fin}>"
