"""Visites pédagogiques et grilles versionnées (lot L6, Partie II D2.3 et D9.3).

Trois objets distincts (D2.3) : le pointage de présence d'un tuteur
(`SupervisorPresenceCheck`), la visite pédagogique (ici) et l'évaluation d'un
élève. Une présence cochée ne remplace pas une visite.

- `GrilleVisite` : grille d'observation versionnée. Une grille publiée
  (`active`) n'est plus modifiable : toute évolution crée une nouvelle version,
  pour que les visites passées restent interprétables avec leur grille.
- `GrilleVisiteItem` : item (O1, P2, LL1…) avec domaine, type et barème.
  Types : `echelle` (0 à bareme_max), `oui_non` (0/1), `outil` (disponibilité
  d'un support), `texte` (observation libre, sans score).
- `Visite` : date, classe, tuteur observé, superviseur, durée éventuelle,
  points d'appui et à améliorer ; scores recalculés par le serveur depuis les
  items applicables.
- `VisiteReponse` : réponse à un item ; « non applicable » et « manquant »
  restent distincts et ne comptent pas comme zéro.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDMixin

STATUTS_GRILLE = ("brouillon", "active", "retiree")
TYPES_ITEM = ("echelle", "oui_non", "outil", "texte")
STATUTS_VISITE = ("soumise", "annulee")


class GrilleVisite(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "grilles_visite"
    __table_args__ = (UniqueConstraint("nom", "version", name="uq_grilles_visite_nom_version"),)

    nom: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    statut: Mapped[str] = mapped_column(String(12), nullable=False, default="brouillon", server_default="brouillon")
    # NULL = utilisable dans toutes les campagnes.
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("program_sessions.id", ondelete="RESTRICT"), nullable=True, index=True,
    )
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    publiee_le: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class GrilleVisiteItem(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "grille_visite_items"
    __table_args__ = (UniqueConstraint("grille_id", "code", name="uq_grille_visite_items_code"),)

    grille_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("grilles_visite.id", ondelete="RESTRICT"), nullable=False, index=True,
    )
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    domaine: Mapped[str] = mapped_column(String(50), nullable=False)
    libelle: Mapped[str] = mapped_column(String(500), nullable=False)
    type: Mapped[str] = mapped_column(String(10), nullable=False, default="echelle")
    bareme_max: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ordre: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    aide: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    obligatoire: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")


class Visite(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "visites"
    __table_args__ = (
        # Idempotence (APP-16) : une clé d'opération client par superviseur.
        UniqueConstraint("superviseur_id", "client_op_id", name="uq_visites_superviseur_client_op"),
    )

    superviseur_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True,
    )
    teacher_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True,
    )
    school_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="RESTRICT"), nullable=True, index=True,
    )
    classe: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("program_sessions.id", ondelete="RESTRICT"), nullable=True, index=True,
    )
    grille_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("grilles_visite.id", ondelete="RESTRICT"), nullable=False, index=True,
    )
    date_visite: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    heure_debut: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)  # HH:MM déclaré
    duree_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    points_appui: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    points_amelioration: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    commentaire: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Scores recalculés par le serveur (items applicables uniquement).
    score_obtenu: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    score_max: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    scores_domaines: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    nb_items_manquants: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    nb_outils_indisponibles: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    statut: Mapped[str] = mapped_column(String(10), nullable=False, default="soumise", server_default="soumise", index=True)
    statut_motif: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    statut_par_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    statut_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    client_op_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    client_op_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class VisiteReponse(Base, UUIDMixin):
    __tablename__ = "visite_reponses"
    __table_args__ = (UniqueConstraint("visite_id", "item_id", name="uq_visite_reponses_item"),)

    visite_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("visites.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("grille_visite_items.id", ondelete="RESTRICT"), nullable=False, index=True,
    )
    valeur: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    disponible: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    texte: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    non_applicable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
