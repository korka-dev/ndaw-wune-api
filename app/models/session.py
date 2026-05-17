from __future__ import annotations

import enum
import uuid
from datetime import date
from typing import List, Optional

from sqlalchemy import Date, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDMixin


class SessionStatus(str, enum.Enum):
    active   = "active"
    inactive = "inactive"


class ProgramSession(Base, UUIDMixin, TimestampMixin):
    """Session / cohorte du programme (ex: Session 1 — Pilote 2024-25)."""
    __tablename__ = "program_sessions"

    name:        Mapped[str]           = mapped_column(String(255), nullable=False)
    date_debut:  Mapped[date]          = mapped_column(Date, nullable=False)
    date_fin:    Mapped[date]          = mapped_column(Date, nullable=False)
    status:      Mapped[SessionStatus] = mapped_column(
        Enum(SessionStatus, name="session_status"),
        default=SessionStatus.inactive,
        nullable=False,
    )
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── Relations ─────────────────────────────────────────────────────────────
    teacher_assignments: Mapped[List["TeacherSession"]] = relationship(
        "TeacherSession",
        back_populates="session",
        cascade="all, delete-orphan",
    )
    planning_segments: Mapped[List["PlanningSegment"]] = relationship(
        "PlanningSegment",
        back_populates="session",
        cascade="all, delete-orphan",
    )
    seances: Mapped[List["Seance"]] = relationship(
        "Seance",
        back_populates="session",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<ProgramSession {self.name!r} [{self.status}]>"


class TeacherSession(Base, TimestampMixin):
    """Table d'association enseignant ↔ session de programme."""
    __tablename__ = "teacher_sessions"

    teacher_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("program_sessions.id", ondelete="CASCADE"),
        primary_key=True,
    )

    # ── Relations ─────────────────────────────────────────────────────────────
    teacher: Mapped["User"]           = relationship("User",           back_populates="teacher_sessions")
    session: Mapped["ProgramSession"] = relationship("ProgramSession", back_populates="teacher_assignments")

    def __repr__(self) -> str:
        return f"<TeacherSession teacher={self.teacher_id} session={self.session_id}>"
