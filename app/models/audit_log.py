"""Modèle AuditLog — historique des modifications effectuées sur la plateforme.

Chaque action de création / modification / suppression effectuée par un
utilisateur sur les routes d'administration est enregistrée ici, avec
l'identité de la personne connectée, la cible, le motif éventuel et la
référence de requête (APP-13). Aucune valeur saisie n'y est recopiée.
"""
from __future__ import annotations

from typing import Optional
import uuid

from sqlalchemy import Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import UUIDMixin, TimestampMixin


class AuditLog(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "audit_logs"
    # Index présents en base (migrations) et déclarés ici pour que
    # `alembic check` reste sans écart (APP-27).
    __table_args__ = (
        Index("ix_audit_logs_created_at", "created_at"),
        Index("ix_audit_logs_cible", "cible_type", "cible_id"),
    )

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

    # APP-13 : cible précise, motif et référence de requête. Les entrées sont
    # protégées en base (trigger) : ni modification ni suppression.
    cible_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    cible_id:   Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    motif:      Mapped[Optional[str]] = mapped_column(Text,       nullable=True)
    request_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    def __repr__(self) -> str:
        return f"<AuditLog {self.user_name!r} {self.action} {self.entity!r}>"
