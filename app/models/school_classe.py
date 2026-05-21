"""Modèle SchoolClasse — classe rattachée à une école (ex : CE1 A, CP B)."""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDMixin


class SchoolClasse(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "school_classes"
    __table_args__ = (
        UniqueConstraint("school_id", "name", name="uq_school_classes_school_name"),
    )

    name:    Mapped[str]           = mapped_column(String(50),  nullable=False)   # ex : "CE1 A"
    niveau:  Mapped[str]           = mapped_column(String(20),  nullable=False)   # ex : "CE1"
    effectif: Mapped[Optional[int]] = mapped_column(nullable=True)                # nb élèves (info optionnelle)

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("schools.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    school: Mapped["School"] = relationship("School", lazy="selectin")

    def __repr__(self) -> str:
        return f"<SchoolClasse {self.name} [{self.school_id}]>"
