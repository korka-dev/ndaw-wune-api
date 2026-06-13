from __future__ import annotations

import uuid
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, field_validator


class RapportQuestionType(str, Enum):
    texte_court    = "texte_court"
    texte_long     = "texte_long"
    nombre         = "nombre"
    oui_non        = "oui_non"
    choix_unique   = "choix_unique"
    choix_multiple = "choix_multiple"


# ── Création ──────────────────────────────────────────────────────────────────

class RapportQuestionCreate(BaseModel):
    label:    str
    type:     RapportQuestionType
    options:  Optional[List[str]] = None
    required: bool = False
    active:   bool = True
    ordre:    int  = 0

    @field_validator("label")
    @classmethod
    def clean_label(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("La question ne peut pas être vide.")
        return v


# ── Mise à jour ───────────────────────────────────────────────────────────────

class RapportQuestionUpdate(BaseModel):
    label:    Optional[str]              = None
    type:     Optional[RapportQuestionType] = None
    options:  Optional[List[str]]        = None
    required: Optional[bool]             = None
    active:   Optional[bool]             = None
    ordre:    Optional[int]              = None

    @field_validator("label")
    @classmethod
    def clean_label(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("La question ne peut pas être vide.")
        return v


# ── Réponse ───────────────────────────────────────────────────────────────────

class RapportQuestionResponse(BaseModel):
    id:       uuid.UUID
    label:    str
    type:     str
    options:  Optional[List[str]] = None
    required: bool
    active:   bool
    ordre:    int

    model_config = {"from_attributes": True}
