"""Modèle RapportQuestion — questions complémentaires configurables par l'admin
et affichées dynamiquement dans le formulaire de rapport journalier (app mobile)."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDMixin


class RapportQuestion(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "rapport_questions"

    label:    Mapped[str]              = mapped_column(String(500), nullable=False)
    # texte_court | texte_long | nombre | oui_non | choix_unique | choix_multiple
    type:     Mapped[str]              = mapped_column(String(30), nullable=False)
    options:  Mapped[Optional[list[str]]] = mapped_column(ARRAY(String), nullable=True)
    required: Mapped[bool]             = mapped_column(Boolean, default=False, nullable=False)
    active:   Mapped[bool]             = mapped_column(Boolean, default=True, nullable=False)
    ordre:    Mapped[int]              = mapped_column(Integer, default=0, nullable=False)

    def __repr__(self) -> str:
        return f"<RapportQuestion {self.label!r}>"
