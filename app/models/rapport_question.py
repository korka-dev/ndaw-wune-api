"""Modèle RapportQuestion — questions complémentaires configurables par l'admin
et affichées dynamiquement dans le formulaire de rapport journalier (app mobile)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDMixin


class RapportQuestion(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "rapport_questions"

    label:    Mapped[str]              = mapped_column(String(500), nullable=False)
    # texte_court | texte_long | nombre | date | oui_non | choix_unique | choix_multiple
    # | selection_eleve | selection_tuteur (sélection dans la liste réelle de
    # l'app — élèves du tuteur, ou tuteurs supervisés côté superviseur)
    type:     Mapped[str]              = mapped_column(String(30), nullable=False)
    options:  Mapped[Optional[list[str]]] = mapped_column(ARRAY(String), nullable=True)
    required: Mapped[bool]             = mapped_column(Boolean, default=False, nullable=False)
    active:   Mapped[bool]             = mapped_column(Boolean, default=True, nullable=False)
    ordre:    Mapped[int]              = mapped_column(Integer, default=0, nullable=False)
    # tuteur | superviseur | tous — rôle mobile à qui la question est destinée
    cible:    Mapped[str]              = mapped_column(
        String(20), default="tuteur", server_default="tuteur", nullable=False
    )

    # Version du sens de la question (libellé, type, options). Toute
    # modification de ces champs crée une nouvelle version (lot L4, D5.2) :
    # une réponse s'interprète avec la version valide à sa réception.
    version:  Mapped[int]              = mapped_column(Integer, default=1, server_default="1", nullable=False)

    def __repr__(self) -> str:
        return f"<RapportQuestion {self.label!r}>"


class RapportQuestionVersion(Base, UUIDMixin):
    """Historique immuable des versions d'une question (D5.2, REC13).

    Sans clé étrangère vers rapport_questions : la définition d'une question
    supprimée reste disponible pour interpréter les anciennes réponses."""
    __tablename__ = "rapport_question_versions"
    __table_args__ = (UniqueConstraint("question_id", "version", name="uq_rapport_question_version"),)

    question_id: Mapped[uuid.UUID]          = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    version:     Mapped[int]                = mapped_column(Integer, nullable=False)
    label:       Mapped[str]                = mapped_column(String(500), nullable=False)
    type:        Mapped[str]                = mapped_column(String(30), nullable=False)
    options:     Mapped[Optional[list[str]]] = mapped_column(ARRAY(String), nullable=True)
    cible:       Mapped[str]                = mapped_column(String(20), nullable=False)
    valid_from:  Mapped[datetime]           = mapped_column(DateTime(timezone=True), nullable=False)
    created_at:  Mapped[datetime]           = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
