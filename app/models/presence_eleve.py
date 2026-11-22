"""Présence individuelle d'un élève à une date (lot L6, Partie II D8 « Assiduité »).

Une ligne = un élève × un jour. Sources :
- `rapport` : déduite du rapport journalier du tuteur. Quand le rapport
  transmet la liste identifiée des absents (`absents_ids`, même vide), chaque
  élève actif des classes du tuteur reçoit « absent » ou « présent ». Un
  rapport sans liste identifiée ne produit AUCUNE ligne : l'appel reste « non
  renseigné », jamais « aucune absence » (D4.4) ;
- `appel` : saisie ou correction explicite (absence excusée, par exemple) ;
  elle n'est jamais écrasée par un rapport ;
- `reprise` : absence importée de l'historique (D10.2).
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Optional

from sqlalchemy import Date, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDMixin

STATUTS_PRESENCE = ("present", "absent", "excuse")
SOURCES_PRESENCE = ("rapport", "appel", "reprise")


class PresenceEleve(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "presences_eleves"
    __table_args__ = (UniqueConstraint("eleve_id", "date_jour", name="uq_presences_eleves_eleve_date"),)

    eleve_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("eleves.id", ondelete="RESTRICT"), nullable=False, index=True,
    )
    date_jour: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("program_sessions.id", ondelete="RESTRICT"), nullable=True, index=True,
    )
    # Auteur de l'appel (tuteur) ; NULL pour une reprise sans auteur identifié.
    teacher_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True, index=True,
    )
    statut: Mapped[str] = mapped_column(String(10), nullable=False)
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    rapport_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rapports_journalier.id", ondelete="RESTRICT"), nullable=True, index=True,
    )
    motif: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    modifie_par_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
