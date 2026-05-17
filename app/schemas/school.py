import uuid
from typing import Optional, List
from pydantic import BaseModel, field_validator


class SchoolCreate(BaseModel):
    name:           str
    region:         Optional[str] = None
    city:           Optional[str] = None
    director:       Optional[str] = None
    director_phone: Optional[str] = None

    @field_validator("name")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Le nom de l'école ne peut pas être vide.")
        return v.strip()


class SchoolUpdate(BaseModel):
    name:           Optional[str] = None
    region:         Optional[str] = None
    city:           Optional[str] = None
    director:       Optional[str] = None
    director_phone: Optional[str] = None


class SchoolResponse(BaseModel):
    id:             uuid.UUID
    name:           str
    region:         Optional[str]
    city:           Optional[str]
    director:       Optional[str]
    director_phone: Optional[str] = None

    model_config = {"from_attributes": True}


class SchoolList(BaseModel):
    total: int
    items: List[SchoolResponse]
