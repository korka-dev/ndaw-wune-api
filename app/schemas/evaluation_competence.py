from __future__ import annotations

import re
import uuid
from typing import Optional

from pydantic import BaseModel, field_validator


def slugify(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", label.strip().lower()).strip("_")
    return slug or "competence"


class EvaluationCompetenceCreate(BaseModel):
    label:  str
    code:   Optional[str] = None
    active: bool = True
    ordre:  int  = 0

    @field_validator("label")
    @classmethod
    def clean_label(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("La compétence ne peut pas être vide.")
        return v


class EvaluationCompetenceUpdate(BaseModel):
    label:  Optional[str]  = None
    code:   Optional[str]  = None
    active: Optional[bool] = None
    ordre:  Optional[int]  = None

    @field_validator("label")
    @classmethod
    def clean_label(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("La compétence ne peut pas être vide.")
        return v


class EvaluationCompetenceResponse(BaseModel):
    id:     uuid.UUID
    label:  str
    code:   str
    active: bool
    ordre:  int

    model_config = {"from_attributes": True}
