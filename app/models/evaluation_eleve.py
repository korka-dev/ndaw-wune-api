"""Modèle EvaluationEleve — évaluation d'un élève par un superviseur de terrain."""
from __future__ import annotations

import uuid
from datetime import date
from typing import Optional

from sqlalchemy import Date, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDMixin


class EvaluationEleve(Base, UUIDMixin, TimestampMixin):
    """
    Enregistre le résultat d'une évaluation de compétence pour un élève,
    réalisée par un superviseur lors d'une visite de terrain.

    - competence  : identifiant de la compétence évaluée (ex: "distinguer_b_d")
    - resultat    : "acquis" | "en_cours" | "a_aider"
    - date_eval   : date de l'évaluation (YYYY-MM-DD)
    """

    __tablename__ = "evaluations_eleves"
    __table_args__ = (
        # Un superviseur ne peut évaluer la même compétence d'un élève qu'une fois par jour
        UniqueConstraint(
            "superviseur_id", "eleve_id", "competence", "date_eval",
            name="uq_eval_superviseur_eleve_competence_date",
        ),
    )

    superviseur_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    eleve_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("eleves.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("program_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    competence: Mapped[str]  = mapped_column(String(100), nullable=False)
    resultat:   Mapped[str]  = mapped_column(String(20),  nullable=False)   # acquis | en_cours | a_aider
    date_eval:  Mapped[date] = mapped_column(Date, nullable=False, index=True)
    commentaire: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Relations (lecture seule)
    superviseur: Mapped["User"]              = relationship("User",           foreign_keys=[superviseur_id])
    eleve:       Mapped["Eleve"]             = relationship("Eleve",          foreign_keys=[eleve_id])
    session:     Mapped[Optional["ProgramSession"]] = relationship("ProgramSession", foreign_keys=[session_id])

    def __repr__(self) -> str:
        return f"<EvaluationEleve eleve={self.eleve_id} comp={self.competence} res={self.resultat}>"
