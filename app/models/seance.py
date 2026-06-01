from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDMixin


class SeanceStatus(str, enum.Enum):
    en_cours = "en_cours"
    terminee = "terminee"
    annulee  = "annulee"
    manquee  = "manquee"   # segment planifié non démarré après son heure de fin


class Seance(Base, UUIDMixin, TimestampMixin):
    """Séance de cours enregistrée par un enseignant via le timer mobile."""
    __tablename__ = "seances"

    teacher_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("program_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    planning_segment_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("planning_segments.id", ondelete="SET NULL"),
        nullable=True,
    )

    # ── Données de la séance ──────────────────────────────────────────────────
    classe:       Mapped[str]           = mapped_column(String(50),  nullable=False)
    matiere:      Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    date_seance:  Mapped[datetime]      = mapped_column(DateTime(timezone=True), nullable=False)

    # Utiliser un Enum PostgreSQL natif pour avoir la contrainte au niveau DB
    status: Mapped[SeanceStatus] = mapped_column(
        Enum(SeanceStatus, name="seance_status"),
        default=SeanceStatus.en_cours,
        nullable=False,
    )

    # Timer (timestamps bruts envoyés par le mobile)
    started_at:  Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    duree_minutes: Mapped[Optional[int]]    = mapped_column(Integer, nullable=True)

    # Pauses — liste d'événements [{paused_at, resumed_at?}]
    # Stockée en JSONB pour éviter une table séparée.
    pauses: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="'[]'::jsonb"
    )
    total_paused_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Effectif
    nb_eleves_presents: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    nb_eleves_total:    Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # ── Relations ─────────────────────────────────────────────────────────────
    teacher: Mapped["User"]           = relationship("User",           back_populates="seances")
    session: Mapped["ProgramSession"] = relationship("ProgramSession", back_populates="seances")
    rapport: Mapped[Optional["RapportProf"]] = relationship(
        "RapportProf",
        back_populates="seance",
        uselist=False,
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Seance {self.classe!r} {self.date_seance} [{self.status}]>"


class RapportProf(Base, UUIDMixin, TimestampMixin):
    """Rapport rédigé par l'enseignant à l'issue d'une séance."""
    __tablename__ = "rapports_prof"

    seance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("seances.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,    # contrainte : 1 rapport maximum par séance
        index=True,
    )
    teacher_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Contenu ───────────────────────────────────────────────────────────────
    contenu:           Mapped[str]           = mapped_column(Text, nullable=False)
    difficultes:       Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    points_positifs:   Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    soumis_en_offline: Mapped[bool]          = mapped_column(Boolean, default=False, nullable=False)

    # ── Relations ─────────────────────────────────────────────────────────────
    seance:  Mapped["Seance"] = relationship("Seance", back_populates="rapport")
    teacher: Mapped["User"]   = relationship("User",   back_populates="rapports_prof")

    def __repr__(self) -> str:
        return f"<RapportProf seance={self.seance_id}>"
