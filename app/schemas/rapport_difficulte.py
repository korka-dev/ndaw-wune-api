from __future__ import annotations

import uuid
from typing import Optional

from pydantic import BaseModel, field_validator


# ── Création ──────────────────────────────────────────────────────────────────

class RapportDifficulteCreate(BaseModel):
    label:  str
    active: bool = True
    ordre:  int  = 0

    @field_validator("label")
    @classmethod
    def clean_label(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("La difficulté ne peut pas être vide.")
        return v


# ── Mise à jour ───────────────────────────────────────────────────────────────

class RapportDifficulteUpdate(BaseModel):
    label:  Optional[str]  = None
    active: Optional[bool] = None
    ordre:  Optional[int]  = None

    @field_validator("label")
    @classmethod
    def clean_label(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("La difficulté ne peut pas être vide.")
        return v


# ── Réponse ───────────────────────────────────────────────────────────────────

class RapportDifficulteResponse(BaseModel):
    id:     uuid.UUID
    label:  str
    active: bool
    ordre:  int

    model_config = {"from_attributes": True}
