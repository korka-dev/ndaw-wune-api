from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, field_validator


class EleveRemplacantCreate(BaseModel):
    """Un tuteur recense lui-même un remplaçant pour une de ses classes.
    Nom et prénom sont tous les deux obligatoires — pas de remplaçant à
    demi identifié dans le vivier."""
    nom:    str
    prenom: str
    classe: str
    genre:  Optional[str] = None  # Garçon | Fille

    @field_validator("nom", "prenom", "classe")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Ce champ ne peut pas être vide.")
        return v.strip()
