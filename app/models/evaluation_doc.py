"""Modèle EvaluationDoc — dossier d'évaluation par langue (Seereer, Pulaar, Wolof…)."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDMixin


class EvaluationDoc(Base, UUIDMixin, TimestampMixin):
    """
    Dossier d'évaluation EGRA créé par l'admin.
    Contient le matériel de lecture (lettres, syllabes, mots) et de maths
    pour une langue donnée.
    """

    __tablename__ = "evaluation_docs"

    langue: Mapped[str] = mapped_column(String(100), nullable=False)
    titre:  Mapped[str] = mapped_column(String(255), nullable=False)

    # Listes stockées en JSON : ["a","l","t",...]
    lettres:    Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    syllabes:   Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    mots:       Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    operations: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Plage de semaines du programme pendant laquelle ce dossier s'applique
    # (NULL = valable pour toutes les semaines, comportement historique).
    # Bornes inclusives, cohérentes avec ProgressionConfig.nb_semaines.
    semaine_debut: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    semaine_fin:   Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    def __repr__(self) -> str:
        return f"<EvaluationDoc langue={self.langue} titre={self.titre!r}>"
