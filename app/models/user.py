from __future__ import annotations

import enum
import uuid
from typing import List, Optional

from sqlalchemy import Boolean, Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDMixin


class UserRole(str, enum.Enum):
    admin         = "admin"
    coordonnateur = "coordonnateur"
    enseignant    = "enseignant"


class UserStatus(str, enum.Enum):
    actif   = "actif"
    inactif = "inactif"


class User(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "users"

    # ── Identité ──────────────────────────────────────────────────────────────
    name:  Mapped[str]           = mapped_column(String(255), nullable=False)
    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # ── Authentification ──────────────────────────────────────────────────────
    email:         Mapped[Optional[str]] = mapped_column(String(255), unique=True, index=True, nullable=True)
    phone:         Mapped[Optional[str]] = mapped_column(String(30),  unique=True, index=True, nullable=True)
    password_hash: Mapped[str]           = mapped_column(String(255), nullable=False)

    # ── Rôle & statut ─────────────────────────────────────────────────────────
    role:   Mapped[UserRole]   = mapped_column(Enum(UserRole,   name="user_role"),   nullable=False)
    status: Mapped[UserStatus] = mapped_column(
        Enum(UserStatus, name="user_status"),
        default=UserStatus.actif,
        nullable=False,
    )

    # ── Premier connexion ─────────────────────────────────────────────────────
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )

    # ── Rattachement école ────────────────────────────────────────────────────
    school_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("schools.id", ondelete="SET NULL"),
        nullable=True,
    )
    niveau:  Mapped[Optional[List[str]]] = mapped_column(ARRAY(String), nullable=True)
    classes: Mapped[Optional[List[str]]] = mapped_column(ARRAY(String), nullable=True)

    # ── Relations ─────────────────────────────────────────────────────────────
    school: Mapped[Optional["School"]] = relationship(
        "School", back_populates="teachers", lazy="selectin"
    )

    teacher_sessions: Mapped[List["TeacherSession"]] = relationship(
        "TeacherSession",
        back_populates="teacher",
        cascade="all, delete-orphan",
    )

    seances: Mapped[List["Seance"]] = relationship(
        "Seance", back_populates="teacher", cascade="all, delete-orphan"
    )

    rapports_prof: Mapped[List["RapportProf"]] = relationship(
        "RapportProf", back_populates="teacher", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<User {self.name!r} [{self.role}]>"
