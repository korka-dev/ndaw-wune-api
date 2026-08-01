"""Modèle EvaluationResultat — une évaluation datée d'un élève tiré au sort.

Le superviseur évalue le même élève plusieurs fois au fil du programme (une
fois par jour de passage). `EvaluationTirage` ne portait qu'un seul résultat :
réévaluer écrasait la mesure précédente, ce qui rendait impossible tout suivi
de progression. Chaque passage est donc enregistré ici, une ligne par (tirage,
date) — réévaluer le même jour corrige la ligne du jour au lieu d'en créer une
seconde.

Le dernier résultat reste recopié sur `EvaluationTirage` : le dashboard admin
et l'app continuent d'afficher l'état courant sans modification.
"""
from __future__ import annotations

import uuid
from datetime import date as date_type
from typing import Optional

from sqlalchemy import Date, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDMixin


class EvaluationResultat(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "evaluation_resultats"
    __table_args__ = (
        UniqueConstraint("tirage_id", "date_eval", name="uq_evaluation_resultat_tirage_date"),
    )

    tirage_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("evaluation_tirages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    superviseur_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    date_eval:      Mapped[date_type]    = mapped_column(Date, nullable=False, index=True)
    resultat:       Mapped[str]          = mapped_column(String(30), nullable=False)
    commentaire:    Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    audio_filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    def __repr__(self) -> str:
        return f"<EvaluationResultat tirage={self.tirage_id} {self.date_eval} {self.resultat}>"
