import uuid
from datetime import date
from typing import Optional, List
from pydantic import BaseModel, field_validator, model_validator
from app.models.session import SessionStatus


# ── ProgramSession ────────────────────────────────────────────────────────────

class SessionCreate(BaseModel):
    name:        str
    date_debut:  date
    date_fin:    date
    description: Optional[str] = None

    @field_validator("name")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Le nom de la session ne peut pas être vide.")
        return v.strip()

    @model_validator(mode="after")
    def dates_coherentes(self):
        if self.date_fin < self.date_debut:
            raise ValueError("La date de fin doit être postérieure à la date de début.")
        return self


class SessionUpdate(BaseModel):
    name:        Optional[str]           = None
    date_debut:  Optional[date]          = None
    date_fin:    Optional[date]          = None
    description: Optional[str]          = None
    status:      Optional[SessionStatus] = None


class SessionResponse(BaseModel):
    id:          uuid.UUID
    name:        str
    date_debut:  date
    date_fin:    date
    status:      SessionStatus
    description: Optional[str]

    model_config = {"from_attributes": True}


class SessionList(BaseModel):
    total: int
    items: List[SessionResponse]


# ── TeacherSession (assignation) ──────────────────────────────────────────────

class TeacherAssignRequest(BaseModel):
    teacher_ids: List[uuid.UUID]


class TeacherSessionResponse(BaseModel):
    teacher_id: uuid.UUID
    session_id: uuid.UUID
    teacher_name: Optional[str] = None

    model_config = {"from_attributes": True}
