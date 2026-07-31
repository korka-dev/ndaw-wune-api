from __future__ import annotations

import uuid

from pydantic import BaseModel, field_validator


class RapportLibelleUpdate(BaseModel):
    texte: str

    @field_validator("texte")
    @classmethod
    def clean_texte(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Le libellé ne peut pas être vide.")
        return v


class RapportLibelleResponse(BaseModel):
    id:    uuid.UUID
    cle:   str
    cible: str
    texte: str

    model_config = {"from_attributes": True}
