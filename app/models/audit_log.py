"""Modèle AuditLog — historique des modifications effectuées sur la plateforme.

Chaque action de création / modification / suppression effectuée par un
utilisateur sur les routes d'administration est enregistrée ici, avec
l'identité de la personne connectée au moment de l'action.
"""
from __future__ import annotations

from typing import Optional
import uuid

from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import UUIDMixin, TimestampMixin


class AuditLog(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "audit_logs"

    user_id:   Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    user_name: Mapped[str]                 = mapped_column(String(150), nullable=False)
    user_role: Mapped[str]                 = mapped_column(String(30),  nullable=False)

    # create | update | delete
    action:    Mapped[str] = mapped_column(String(20),  nullable=False)
    # Ressource concernée (ex: "Enseignants", "Écoles", "Planning"...)
    entity:    Mapped[str] = mapped_column(String(100), nullable=False)

    method:      Mapped[str]           = mapped_column(String(10),  nullable=False)
    path:        Mapped[str]           = mapped_column(String(255), nullable=False)
    description: Mapped[str]           = mapped_column(Text,        nullable=False)

    def __repr__(self) -> str:
        return f"<AuditLog {self.user_name!r} {self.action} {self.entity!r}>"
