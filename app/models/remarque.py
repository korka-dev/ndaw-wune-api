"""Modèle Remarque — problèmes signalés par les utilisateurs de l'app mobile.

Remarques hors rapport (ex: manque de matériel, problème de local…) saisies
par les tuteurs/superviseurs via l'assistant de signalement de l'application,
et consultées par l'admin depuis le tableau de bord.
"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDMixin


class Remarque(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "remarques"

    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    user_name: Mapped[str] = mapped_column(String(150), nullable=False)
    user_role: Mapped[str] = mapped_column(String(30),  nullable=False)
    ecole:     Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Catégorie du problème (ex: "materiel", "local", "eleves", "autre")
    categorie: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    message:   Mapped[str] = mapped_column(Text, nullable=False)

    # nouveau | traite
    statut: Mapped[str] = mapped_column(String(20), default="nouveau", server_default="nouveau", nullable=False, index=True)

    user: Mapped[Optional["User"]] = relationship("User", foreign_keys=[user_id])

    def __repr__(self) -> str:
        return f"<Remarque {self.user_name!r} [{self.categorie}]>"
