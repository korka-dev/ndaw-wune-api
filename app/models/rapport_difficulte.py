"""Modèle RapportDifficulte — liste des difficultés proposées dans l'étape
« Difficultés » du rapport journalier (app mobile), configurable par l'admin."""
from __future__ import annotations

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDMixin


class RapportDifficulte(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "rapport_difficultes"

    label:  Mapped[str]  = mapped_column(String(500), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    ordre:  Mapped[int]  = mapped_column(Integer, default=0, nullable=False)

    def __repr__(self) -> str:
        return f"<RapportDifficulte {self.label!r}>"
