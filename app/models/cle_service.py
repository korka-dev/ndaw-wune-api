"""Clés d'accès de service (lot L6, D9.4 « actualisation du dashboard par API »).

Un outil externe (dashboard R Shiny, script Stata) lit l'API analytique sans
compte personnel. La clé est limitée (portées, lecture seule, données
pseudonymisées uniquement), datée, révocable, et seule son empreinte SHA-256
est conservée : la valeur n'est montrée qu'une fois, à la création.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDMixin

PORTEES_CLE = ("analytique", "tableau_bord")


class CleService(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "cles_service"

    nom: Mapped[str] = mapped_column(String(200), nullable=False)
    prefixe: Mapped[str] = mapped_column(String(12), nullable=False, index=True)
    empreinte: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    portees: Mapped[List[str]] = mapped_column(ARRAY(String), nullable=False)
    expire_le: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revoquee_le: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cree_par_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    derniere_utilisation: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
