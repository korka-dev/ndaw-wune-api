"""Modèle EvaluationCompetence — compétences d'évaluation configurables par l'admin
et affichées dynamiquement dans l'écran d'évaluation du superviseur (app mobile)."""
from __future__ import annotations

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDMixin


class EvaluationCompetence(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "evaluation_competences"

    label:  Mapped[str]  = mapped_column(String(200), nullable=False)
    # code court et stable, utilisé comme identifiant de compétence dans evaluations_eleves.competence
    code:   Mapped[str]  = mapped_column(String(50), unique=True, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    ordre:  Mapped[int]  = mapped_column(Integer, default=0, nullable=False)

    def __repr__(self) -> str:
        return f"<EvaluationCompetence {self.code!r}>"
