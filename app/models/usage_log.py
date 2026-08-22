"""Modèle UsageLog — traçage des fonctionnalités utilisées dans l'app mobile.

Chaque ouverture d'une fonctionnalité (planning, rapports, évaluation…) par un
tuteur ou un superviseur est enregistrée ici afin que l'admin puisse savoir
quelles fonctionnalités sont les plus utilisées.
"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDMixin


class UsageLog(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "usage_logs"

    user_id:   Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    user_name: Mapped[str]                 = mapped_column(String(150), nullable=False)
    user_role: Mapped[str]                 = mapped_column(String(30),  nullable=False)
    # Code acteur (téléphone au moment du log) — identifiant stable en plus du
    # nom en clair, qui peut changer ou être ambigu entre deux personnes.
    user_code: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)

    # Identifiant de la fonctionnalité. Pour le timer, les 4 actions du cycle
    # de vie sont journalisées séparément (timer_start/pause/resume/stop) au
    # lieu d'un unique libellé "timer" — permet de reconstruire précisément
    # le déroulé d'une séance depuis les logs.
    feature:   Mapped[str] = mapped_column(String(100), nullable=False, index=True)

    # Relie l'événement timer à la séance concernée (donc au rapport
    # correspondant via Seance.rapport) — NULL pour les événements hors timer.
    seance_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    # Durée de la séance calculée par l'app, en secondes — renseignée sur
    # l'événement timer_stop.
    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    def __repr__(self) -> str:
        return f"<UsageLog {self.user_name!r} {self.feature!r}>"
