"""Données de recherche séparées (lot L6 : APP-33 et D9.4).

`AssignationRecherche` est l'historique immuable des assignations du protocole
(groupe traitement / contrôle) et des droits d'accès effectifs au minuteur.
Chaque changement est une nouvelle ligne datée, attribuée et motivée ; la table
est protégée en base contre toute modification ou suppression (même mécanisme
que l'historique APP-13).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import UUIDMixin


class AssignationRecherche(Base, UUIDMixin):
    __tablename__ = "assignations_recherche"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True,
    )
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("program_sessions.id", ondelete="RESTRICT"), nullable=True, index=True,
    )
    groupe_recherche: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    app_access: Mapped[str] = mapped_column(String(20), nullable=False)
    # Exception au protocole (accès différent de la matrice) : toujours motivée.
    exception: Mapped[bool] = mapped_column(nullable=False, default=False)
    motif: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    auteur_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    effective_le: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), index=True,
    )
