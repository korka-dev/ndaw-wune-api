"""Modèle EvaluationTirage — tirage aléatoire d'élèves pour un sujet d'évaluation."""
from __future__ import annotations

import uuid
from datetime import date
from typing import Optional

from sqlalchemy import Boolean, Date, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDMixin


class EvaluationTirage(Base, UUIDMixin, TimestampMixin):
    """
    Résultat du tirage aléatoire : un élève sélectionné pour un sujet d'évaluation.
    Le superviseur évalue cet élève et peut enregistrer sa réponse audio.
    """
    __tablename__ = "evaluation_tirages"

    sujet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("evaluation_sujets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    eleve_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("eleves.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Superviseur qui a évalué l'élève (rempli à la soumission)
    superviseur_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Présence de l'élève tiré au sort (None = pas encore vérifiée par le superviseur)
    present: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)

    # Résultat de l'évaluation : reussi | intermediaire | pas_reussi (anciens : acquis | a_aider)
    resultat: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    commentaire: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    date_eval: Mapped[Optional[date]] = mapped_column(Date, nullable=True, index=True)

    # Enregistrement audio (chemin relatif dans UPLOADS_DIR)
    audio_filename: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    # Relations
    sujet: Mapped["EvaluationSujet"] = relationship("EvaluationSujet", back_populates="tirages")
    eleve: Mapped["Eleve"] = relationship("Eleve", foreign_keys=[eleve_id])
    superviseur: Mapped[Optional["User"]] = relationship("User", foreign_keys=[superviseur_id])

    def __repr__(self) -> str:
        return f"<EvaluationTirage sujet={self.sujet_id} eleve={self.eleve_id}>"
