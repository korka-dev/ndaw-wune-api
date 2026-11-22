"""Reprise de l'historique Kobo / CSV (lot L6, Partie II D10.2 et Annexe A).

- `LotReprise` : un import, rattaché à une copie figée des sources (empreinte
  SHA-256 de chaque fichier), avec ses compteurs et sa réconciliation.
- `CorrespondanceReprise` : table de correspondance validée par une personne
  entre un code source (tuteur, école, élève…) et un identifiant de la base.
- `RepriseLigne` : ligne source conservée telle quelle (espace protégé), avec
  son statut (importée, quarantaine, doublon, rejetée) et la raison. Les
  observations brutes ne sont jamais modifiées ; seules les lignes validées
  produisent des données exploitables.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDMixin

TYPES_CORRESPONDANCE = ("tuteur", "superviseur", "ecole", "eleve")
STATUTS_LIGNE = ("importee", "quarantaine", "doublon", "rejetee")


class LotReprise(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "lots_reprise"

    nom: Mapped[str] = mapped_column(String(200), nullable=False)
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("program_sessions.id", ondelete="RESTRICT"), nullable=True, index=True,
    )
    # Fichiers figés : [{fichier, sha256, lignes, octets}]
    sources: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    statut: Mapped[str] = mapped_column(String(15), nullable=False, default="analyse")  # analyse | importe | annule
    compteurs: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    reconciliation: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    commentaire: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cree_par: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    importe_le: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class CorrespondanceReprise(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "correspondances_reprise"
    __table_args__ = (UniqueConstraint("type", "code_source", name="uq_correspondances_reprise_type_code"),)

    type: Mapped[str] = mapped_column(String(15), nullable=False)
    code_source: Mapped[str] = mapped_column(String(255), nullable=False)
    libelle_source: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    cible_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    valide_par: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    commentaire: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class RepriseLigne(Base, UUIDMixin):
    __tablename__ = "reprise_lignes"
    __table_args__ = (
        UniqueConstraint("lot_id", "fichier", "numero_ligne", name="uq_reprise_lignes_position"),
    )

    lot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("lots_reprise.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    fichier: Mapped[str] = mapped_column(String(100), nullable=False)
    numero_ligne: Mapped[int] = mapped_column(Integer, nullable=False)
    source_submission_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    source_parent_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    donnees: Mapped[dict] = mapped_column(JSONB, nullable=False)
    statut: Mapped[str] = mapped_column(String(12), nullable=False)
    motif: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cible_type: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    cible_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
