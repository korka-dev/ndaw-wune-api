from __future__ import annotations

import uuid
from datetime import date
from typing import Optional

from pydantic import BaseModel, field_validator


class EleveRemplacementCreate(BaseModel):
    """Remplacement d'un titulaire par un remplaçant déjà recensé dans la
    même classe (Base NWV 2026 / RCT) — plus de saisie libre de nom : le
    tuteur choisit dans la liste des remplaçants disponibles."""
    ancien_eleve_id:  uuid.UUID
    nouveau_eleve_id: uuid.UUID
    motif:            str = "Remplacement saisi par le tuteur"

    @field_validator("motif")
    @classmethod
    def not_empty(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("Ce champ ne peut pas être vide.")
        return v


class EleveRemplacementResponse(BaseModel):
    id:                 uuid.UUID
    ancien_eleve_id:    Optional[uuid.UUID]
    ancien_eleve_nom:   Optional[str]
    nouveau_eleve_id:   Optional[uuid.UUID]
    nouveau_eleve_nom:  str
    motif:              str
    teacher_id:         uuid.UUID
    school_id:          Optional[uuid.UUID]
    classe:             str
    date_remplacement:  date

    model_config = {"from_attributes": True}
