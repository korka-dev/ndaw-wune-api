from typing import List, Optional
from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base
from app.models.base import UUIDMixin, TimestampMixin


class School(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "schools"
    __table_args__ = (
        UniqueConstraint("director_phone", name="uq_schools_director_phone"),
    )

    name:            Mapped[str]           = mapped_column(String(255), nullable=False)
    code_ecole:      Mapped[Optional[int]] = mapped_column(nullable=True, unique=True, index=True)
    region:          Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    city:            Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    director:        Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    director_phone:  Mapped[Optional[str]] = mapped_column(String(30),  nullable=True)

    # ── Relations ─────────────────────────────────────────────────
    teachers: Mapped[List["User"]] = relationship("User", back_populates="school")

    def __repr__(self):
        return f"<School {self.name}>"
