"""Modèle Rapport Journalier — soumis par le tuteur/superviseur depuis l'app mobile."""
from __future__ import annotations

import uuid
from datetime import date
from typing import Optional

from sqlalchemy import Boolean, Date, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDMixin


class RapportJournalier(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "rapports_journalier"

    teacher_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Données administratives ───────────────────────────────────────────────
    date_rapport: Mapped[date]           = mapped_column(Date, nullable=False)
    ief:          Mapped[str]            = mapped_column(String(200), nullable=False)
    commune:      Mapped[str]            = mapped_column(String(200), nullable=False)
    ecole:        Mapped[str]            = mapped_column(String(255), nullable=False)
    superviseur:  Mapped[str]            = mapped_column(String(255), nullable=False)
    nom_tuteur:   Mapped[str]            = mapped_column(String(255), nullable=False)

    # ── Présences ─────────────────────────────────────────────────────────────
    nb_absences:  Mapped[int]            = mapped_column(Integer, default=0, nullable=False)
    absents:      Mapped[Optional[str]]  = mapped_column(Text, nullable=True)  # JSON list
    semaine:      Mapped[int]            = mapped_column(Integer, nullable=False)
    jour_cours:   Mapped[int]            = mapped_column(Integer, nullable=False)

    # ── Difficultés ───────────────────────────────────────────────────────────
    difficultes:              Mapped[str]            = mapped_column(Text, nullable=False)  # JSON list
    autres_difficultes:       Mapped[Optional[str]]  = mapped_column(Text, nullable=True)
    description_difficultes:  Mapped[Optional[str]]  = mapped_column(Text, nullable=True)

    # ── Observations ─────────────────────────────────────────────────────────
    directeur_venu:   Mapped[bool]          = mapped_column(Boolean, nullable=False)
    besoin_appui:     Mapped[bool]          = mapped_column(Boolean, nullable=False)
    domaines_appui:   Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON list
    has_observations: Mapped[bool]          = mapped_column(Boolean, default=False, nullable=False)
    commentaires:     Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── Métadonnées ──────────────────────────────────────────────────────────
    soumis_en_offline: Mapped[bool]          = mapped_column(Boolean, default=True, nullable=False)
    photo_classe_url:  Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # ancien champ — 1 seule photo
    photos_classe_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # liste JSON — jusqu'à 3 photos
    reponses_questions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON {question_id: réponse} — questions dynamiques configurées par l'admin

    # ── Relations ─────────────────────────────────────────────────────────────
    teacher: Mapped["User"] = relationship("User")

    def __repr__(self) -> str:
        return f"<RapportJournalier {self.date_rapport} [{self.ecole}]>"
