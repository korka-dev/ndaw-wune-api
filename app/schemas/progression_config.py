from __future__ import annotations

import uuid
from typing import Optional

from pydantic import BaseModel, field_validator


class ProgressionConfigCreate(BaseModel):
    school_id:   Optional[uuid.UUID] = None
    session_id:  Optional[uuid.UUID] = None
    nb_semaines: int = 10
    nb_jours:    int = 3

    @field_validator("nb_semaines", "nb_jours")
    @classmethod
    def must_be_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("La valeur doit être supérieure à 0.")
        return v


class ProgressionConfigUpdate(BaseModel):
    school_id:   Optional[uuid.UUID] = None
    session_id:  Optional[uuid.UUID] = None
    nb_semaines: Optional[int] = None
    nb_jours:    Optional[int] = None

    @field_validator("nb_semaines", "nb_jours")
    @classmethod
    def must_be_positive(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v < 1:
            raise ValueError("La valeur doit être supérieure à 0.")
        return v


class ProgressionConfigResponse(BaseModel):
    id:          uuid.UUID
    school_id:   Optional[uuid.UUID]
    session_id:  Optional[uuid.UUID]
    nb_semaines: int
    nb_jours:    int

    model_config = {"from_attributes": True}
