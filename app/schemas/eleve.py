import uuid
from typing import Optional
from pydantic import BaseModel, field_validator


class EleveCreate(BaseModel):
    nom:            str
    prenom:         Optional[str]       = None
    classe:         str
    genre:          Optional[str]       = None   # Garçon | Fille
    date_naissance: Optional[str]       = None   # ISO YYYY-MM-DD
    school_id:      Optional[uuid.UUID] = None
    session_id:     Optional[uuid.UUID] = None

    @field_validator("nom", "classe")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Ce champ ne peut pas être vide.")
        return v.strip()


class EleveUpdate(BaseModel):
    nom:            Optional[str]       = None
    prenom:         Optional[str]       = None
    classe:         Optional[str]       = None
    genre:          Optional[str]       = None
    date_naissance: Optional[str]       = None
    statut:         Optional[str]       = None   # actif | inactif
    school_id:      Optional[uuid.UUID] = None
    session_id:     Optional[uuid.UUID] = None


class EleveResponse(BaseModel):
    id:             uuid.UUID
    nom:            str
    prenom:         Optional[str]
    classe:         str
    genre:          Optional[str]       = None
    date_naissance: Optional[str]       = None
    statut:         str                 = "actif"
    school_id:      Optional[uuid.UUID]
    session_id:     Optional[uuid.UUID]

    model_config = {"from_attributes": True}
