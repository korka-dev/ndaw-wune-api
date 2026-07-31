"""Modèle RapportLibelle — libellés des champs fixes des rapports (tuteur et
superviseur), éditables par l'admin sans toucher au code de l'app mobile.

Contrairement à RapportQuestion (liste dynamique de questions complémentaires
ajoutées/supprimées librement), ces libellés correspondent à un ensemble FIXE
de clés (une par champ structurel du formulaire — ex: "Y a-t-il des absences
aujourd'hui ?") : l'admin peut modifier le TEXTE affiché, mais pas ajouter ou
supprimer une clé. Les clés sont pré-remplies par la migration Alembic."""
from __future__ import annotations

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDMixin


class RapportLibelle(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "rapport_libelles"

    # Clé stable (ex: "tuteur.absences_question") — référencée par l'app mobile.
    cle:   Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    # tuteur | superviseur
    cible: Mapped[str] = mapped_column(String(20), nullable=False)
    texte: Mapped[str] = mapped_column(Text, nullable=False)

    def __repr__(self) -> str:
        return f"<RapportLibelle {self.cle!r}>"
