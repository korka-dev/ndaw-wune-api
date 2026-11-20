from typing import List, Optional
from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base
from app.models.base import UUIDMixin, TimestampMixin


class School(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "schools"
    __table_args__ = (
        UniqueConstraint("director_phone", name="uq_schools_director_phone"),
        UniqueConstraint("code_ecole", name="uq_schools_code_ecole"),
    )

    name:            Mapped[str]           = mapped_column(String(255), nullable=False)
    # APP-15 : archivage (non NULL = archivé). Rien n'est supprimé avec l'historique.
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    code_ecole:      Mapped[Optional[int]] = mapped_column(nullable=True, index=True)  # unicité : uq_schools_code_ecole
    region:          Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    city:            Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    director:        Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    director_phone:  Mapped[Optional[str]] = mapped_column(String(30),  nullable=True)
    langue:          Mapped[Optional[str]] = mapped_column(String(50),  nullable=True)  # langue nationale d'enseignement (ex: pulaar)

    # ── Relations ─────────────────────────────────────────────────
    teachers: Mapped[List["User"]] = relationship("User", back_populates="school")

    def __repr__(self):
        return f"<School {self.name}>"
