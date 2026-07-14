"""Modèle UsageLog — traçage des fonctionnalités utilisées dans l'app mobile.

Chaque ouverture d'une fonctionnalité (planning, rapports, évaluation…) par un
tuteur ou un superviseur est enregistrée ici afin que l'admin puisse savoir
quelles fonctionnalités sont les plus utilisées.
"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDMixin


class UsageLog(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "usage_logs"

    user_id:   Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    user_name: Mapped[str]                 = mapped_column(String(150), nullable=False)
    user_role: Mapped[str]                 = mapped_column(String(30),  nullable=False)

    # Identifiant de la fonctionnalité (ex: "planning", "rapport_journalier", "evaluation"…)
    feature:   Mapped[str] = mapped_column(String(100), nullable=False, index=True)

    def __repr__(self) -> str:
        return f"<UsageLog {self.user_name!r} {self.feature!r}>"
