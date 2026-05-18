import uuid
from typing import Optional, List
from pydantic import BaseModel, field_validator


class EleveCreate(BaseModel):
    nom:        str
    prenom:     Optional[str]       = None
    classe:     str
    school_id:  Optional[uuid.UUID] = None
    session_id: Optional[uuid.UUID] = None

    @field_validator("nom", "classe")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Ce champ ne peut pas être vide.")
        return v.strip()


class EleveUpdate(BaseModel):
    nom:        Optional[str]       = None
    prenom:     Optional[str]       = None
    classe:     Optional[str]       = None
    school_id:  Optional[uuid.UUID] = None
    session_id: Optional[uuid.UUID] = None


class EleveResponse(BaseModel):
    id:         uuid.UUID
    nom:        str
    prenom:     Optional[str]
    classe:     str
    school_id:  Optional[uuid.UUID]
    session_id: Optional[uuid.UUID]

    model_config = {"from_attributes": True}
