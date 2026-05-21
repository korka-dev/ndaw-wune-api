from __future__ import annotations

import uuid
from typing import List, Optional

from pydantic import BaseModel, field_validator


# ── Création ──────────────────────────────────────────────────────────────────

class SchoolClasseCreate(BaseModel):
    name:      str
    niveau:    str
    school_id: uuid.UUID
    effectif:  Optional[int] = None

    @field_validator("name")
    @classmethod
    def clean_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Le nom de la classe ne peut pas être vide.")
        return v

    @field_validator("niveau")
    @classmethod
    def clean_niveau(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Le niveau ne peut pas être vide.")
        return v


# ── Mise à jour ───────────────────────────────────────────────────────────────

class SchoolClasseUpdate(BaseModel):
    name:     Optional[str] = None
    niveau:   Optional[str] = None
    effectif: Optional[int] = None

    @field_validator("name")
    @classmethod
    def clean_name(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v

    @field_validator("niveau")
    @classmethod
    def clean_niveau(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v


# ── Réponse ───────────────────────────────────────────────────────────────────

class SchoolBrief(BaseModel):
    id:   uuid.UUID
    name: str
    model_config = {"from_attributes": True}


class SchoolClasseResponse(BaseModel):
    id:        uuid.UUID
    name:      str
    niveau:    str
    effectif:  Optional[int]
    school_id: uuid.UUID
    school:    Optional[SchoolBrief] = None

    model_config = {"from_attributes": True}


# ── Pagination ────────────────────────────────────────────────────────────────

class SchoolClasseList(BaseModel):
    total: int
    items: List[SchoolClasseResponse]
