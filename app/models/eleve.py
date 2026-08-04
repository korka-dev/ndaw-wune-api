"""Modèle Élève — apprenant suivi dans le programme ARED NdawWune."""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDMixin


class Eleve(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "eleves"
    __table_args__ = (
        UniqueConstraint("school_id", "classe", "nom", "prenom", name="uq_eleve_school_classe_nom"),
    )

    nom:             Mapped[str]           = mapped_column(String(100), nullable=False)
    prenom:          Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    code_eleve:      Mapped[Optional[str]] = mapped_column(String(30),  nullable=True, index=True)
    classe:          Mapped[str]           = mapped_column(String(50),  nullable=False)
    genre:           Mapped[Optional[str]] = mapped_column(String(20),  nullable=True)
    date_naissance:  Mapped[Optional[str]] = mapped_column(String(10),  nullable=True)  # ISO YYYY-MM-DD
    statut:          Mapped[str]           = mapped_column(String(20),  nullable=False, default="actif", server_default="actif")

    # ── Base NWV 2026 (RCT) — Titulaire/Remplaçant + groupes de méthode ────────
    # Ne pas confondre avec User.groupe_recherche (Traitement/Contrôle minuteur,
    # côté enseignant) : ici c'est l'assignation par élève de la méthode de
    # lecture et de maths.
    statut_selection: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    groupe_lecture:   Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    groupe_maths:     Mapped[Optional[str]] = mapped_column(String(30), nullable=True)

    school_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("schools.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("program_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    school:  Mapped[Optional["School"]]         = relationship("School")
    session: Mapped[Optional["ProgramSession"]] = relationship("ProgramSession")

    def __repr__(self) -> str:
        return f"<Eleve {self.nom} {self.prenom or ''} [{self.classe}]>"
