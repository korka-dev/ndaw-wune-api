import uuid
from datetime import time
from typing import Optional
from sqlalchemy import String, Integer, Time, ForeignKey, UniqueConstraint
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

    jour:        Mapped[int]            = mapped_column(Integer, nullable=False)
    heure_debut: Mapped[time]           = mapped_column(Time, nullable=False)
    heure_fin:   Mapped[time]           = mapped_column(Time, nullable=False)
    classe:      Mapped[Optional[str]]  = mapped_column(String(50), nullable=True)
    matiere:     Mapped[Optional[str]]  = mapped_column(String(100), nullable=True)

    __table_args__ = (
        UniqueConstraint("session_id", "jour", "heure_debut", name="uq_planning_segment"),
    )

    session: Mapped["ProgramSession"] = relationship("ProgramSession", back_populates="planning_segments")
    teacher: Mapped["User"]           = relationship("User")
    school:  Mapped[Optional["School"]] = relationship("School")

    def __repr__(self):
        return f"<PlanningSegment jour={self.jour} {self.heure_debut}-{self.heure_fin}>"
