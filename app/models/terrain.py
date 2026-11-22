"""Lot L7 — retours des tuteurs et superviseurs (enquêtes NWV 2026).

- `ContactApp` (RET15, RET13) : dernière synchronisation connue de chaque
  compte mobile — version installée et accès au minuteur effectivement
  transmis. Sert à comparer l'assignation de recherche, les droits serveur et
  ce que le téléphone a reçu, et à indiquer la fraîcheur des données d'un
  tuteur (une absence de synchronisation n'est pas une absence de travail).
- `PropositionGroupe` (RET07) : proposition de changement de groupe de
  lecture ou de mathématiques d'un élève ; l'ancien groupe et la date sont
  conservés, aucune assignation de recherche n'est touchée.
- `EvaluationAbsence` (RET04) : élève tiré au sort absent le jour de
  l'évaluation — enregistré comme tel, sans substitution automatique.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDMixin


class ContactApp(Base):
    __tablename__ = "contacts_app"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    version_app: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    derniere_sync_le: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Accès au minuteur transmis au téléphone lors de cette synchronisation.
    minuteur_expose: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    app_access_expose: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)


class PropositionGroupe(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "propositions_groupe"

    eleve_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("eleves.id", ondelete="CASCADE"), nullable=False, index=True)
    domaine: Mapped[str] = mapped_column(String(10), nullable=False)          # lecture | maths
    ancien_groupe: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    nouveau_groupe: Mapped[str] = mapped_column(String(30), nullable=False)
    motif: Mapped[str] = mapped_column(Text, nullable=False)
    # proposee → validee (groupe appliqué) | refusee (motif_refus)
    statut: Mapped[str] = mapped_column(String(15), nullable=False, default="proposee",
                                        server_default="proposee", index=True)
    propose_par_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    decide_par_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decide_le: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    motif_refus: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class EvaluationAbsence(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "evaluation_absences"
    __table_args__ = (UniqueConstraint("eleve_id", "date_eval", name="uq_evaluation_absence_eleve_date"),)

    eleve_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("eleves.id", ondelete="CASCADE"), nullable=False, index=True)
    superviseur_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("program_sessions.id", ondelete="SET NULL"), nullable=True)
    date_eval: Mapped[date] = mapped_column(Date, nullable=False, index=True)
