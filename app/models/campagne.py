"""Paramétrage d'une campagne (lot L6, Partie II D2.2, D8 et Annexe D).

Rien n'est imposé à toutes les campagnes : sans calendrier ni obligation
configurés, les vues « soumissions attendues » et « assiduité » se déclarent
non calculables au lieu d'utiliser des valeurs historiques (trois rapports,
classe de 30 élèves, calendrier fixe).

- `CampagneJour` : jours de cours attendus d'une campagne, avec les exceptions
  (jour férié, grève…) et leur motif.
- `CampagneObligation` : ce que chaque rôle doit produire (rapport journalier,
  pointage, visite), à quelle fréquence et avec quel délai de transmission.
  Chaque modification incrémente la version (règle traçable).
- `SeuilAlerte` : seuils d'alerte configurables, avec leur règle écrite. Une
  ligne sans campagne s'applique à toutes les campagnes qui n'en ont pas.
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Optional

from sqlalchemy import Boolean, Date, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDMixin

ROLES_OBLIGATION = ("enseignant", "superviseur")
OBJETS_OBLIGATION = ("rapport_journalier", "pointage", "visite")
PERIODICITES = ("jour", "semaine")


class CampagneJour(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "campagne_jours"
    __table_args__ = (UniqueConstraint("session_id", "date_jour", name="uq_campagne_jours_session_date"),)

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("program_sessions.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    date_jour: Mapped[date] = mapped_column(Date, nullable=False)
    # False = jour exclu (exception) : il ne compte ni dans les soumissions
    # attendues ni dans les jours d'appel attendus.
    attendu: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    motif: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class CampagneObligation(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "campagne_obligations"
    __table_args__ = (
        UniqueConstraint("session_id", "role", "objet", name="uq_campagne_obligation"),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("program_sessions.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)        # enseignant | superviseur
    objet: Mapped[str] = mapped_column(String(30), nullable=False)       # rapport_journalier | pointage | visite
    periodicite: Mapped[str] = mapped_column(String(10), nullable=False)  # jour | semaine
    quantite: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Délai de transmission accepté (jours après la date de l'activité).
    delai_jours: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    commentaire: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class SeuilAlerte(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "seuils_alerte"
    __table_args__ = (
        # Une valeur par code et par campagne ; une seule valeur globale par code.
        UniqueConstraint("session_id", "code", name="uq_seuils_alerte_session_code"),
        Index("uq_seuils_alerte_global_code", "code", unique=True, postgresql_where=text("session_id IS NULL")),
    )

    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("program_sessions.id", ondelete="CASCADE"), nullable=True, index=True,
    )
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    valeur: Mapped[float] = mapped_column(Float, nullable=False)
    regle: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
