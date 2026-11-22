"""Demandes d'accompagnement et leur circuit de traitement (lot L6, D9.3 / D9.4).

Circuit : ouverture → affectation d'un responsable → traitement → résolution
(ou abandon motivé). Chaque étape est un événement daté et attribué ; rien
n'est réécrit. Origines :
- `visite`     : recommandation issue d'une visite pédagogique ;
- `difficulte` : difficulté signalée dans un rapport journalier (articulée
  avec `RapportDifficulteResolution` : résoudre la demande résout la difficulté) ;
- `besoin`     : domaine d'appui demandé dans un rapport journalier ;
- `manuel`     : demande créée directement.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import Date, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDMixin

ORIGINES_DEMANDE = ("visite", "difficulte", "besoin", "manuel")
STATUTS_DEMANDE = ("ouverte", "assignee", "en_cours", "resolue", "abandonnee")
STATUTS_DEMANDE_OUVERTS = ("ouverte", "assignee", "en_cours")


class DemandeAppui(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "demandes_appui"
    __table_args__ = (
        Index("ix_demandes_appui_statut_echeance", "statut", "echeance"),
    )

    origine: Mapped[str] = mapped_column(String(12), nullable=False, index=True)
    visite_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("visites.id", ondelete="RESTRICT"), nullable=True, index=True,
    )
    rapport_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rapports_journalier.id", ondelete="RESTRICT"), nullable=True, index=True,
    )
    # Difficulté ou domaine d'appui d'origine (libellé tel que signalé).
    libelle_source: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    domaine: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, index=True)
    titre: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    beneficiaire_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True, index=True,
    )
    school_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="RESTRICT"), nullable=True, index=True,
    )
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("program_sessions.id", ondelete="RESTRICT"), nullable=True, index=True,
    )
    responsable_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True, index=True,
    )
    echeance: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    statut: Mapped[str] = mapped_column(String(12), nullable=False, default="ouverte", server_default="ouverte", index=True)
    cree_par_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    cloturee_le: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class DemandeAppuiEvenement(Base, UUIDMixin):
    __tablename__ = "demande_appui_evenements"

    demande_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("demandes_appui.id", ondelete="RESTRICT"), nullable=False, index=True,
    )
    # creation | affectation | statut | commentaire | echeance
    type: Mapped[str] = mapped_column(String(15), nullable=False)
    ancien_statut: Mapped[Optional[str]] = mapped_column(String(12), nullable=True)
    nouveau_statut: Mapped[Optional[str]] = mapped_column(String(12), nullable=True)
    responsable_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    commentaire: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    auteur_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc),
    )
