from __future__ import annotations

import re
import uuid
from typing import List, Optional

from pydantic import BaseModel, field_validator, model_validator

from app.models.user import UserRole, UserStatus


def _normalize_sn_phone(v: Optional[str]) -> Optional[str]:
    """Normalise vers 9 chiffres locaux. Accepte +221, 00221, avec espaces."""
    if v is None:
        return None
    digits = re.sub(r"\D", "", v.strip())
    if len(digits) == 12 and digits.startswith("221"):
        digits = digits[3:]
    elif len(digits) == 14 and digits.startswith("00221"):
        digits = digits[5:]
    if not digits:
        return None
    if len(digits) != 9:
        raise ValueError("Le numéro de téléphone doit contenir exactement 9 chiffres.")
    return digits


# ── Création ──────────────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    name:      str
    title:     Optional[str]       = None
    email:     Optional[str]       = None
    phone:     Optional[str]       = None
    # Le mot de passe est optionnel à la création : si absent, le service
    # applique le mot de passe par défaut (P@sser123) et force le changement.
    password:  Optional[str]       = None
    role:      UserRole
    school_id: Optional[uuid.UUID] = None
    niveau:    Optional[List[str]] = None
    classes:   Optional[List[str]] = None

    @field_validator("name")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Le nom ne peut pas être vide.")
        return v.strip()

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, v: Optional[str]) -> Optional[str]:
        return _normalize_sn_phone(v)

    @model_validator(mode="after")
    def email_or_phone_required(self) -> "UserCreate":
        if not self.email and not self.phone:
            raise ValueError("Un e-mail ou un numéro de téléphone est requis.")
        return self


# ── Mise à jour ───────────────────────────────────────────────────────────────

class UserUpdate(BaseModel):
    name:      Optional[str]        = None
    title:     Optional[str]        = None
    email:     Optional[str]        = None
    phone:     Optional[str]        = None
    password:  Optional[str]        = None
    status:    Optional[UserStatus] = None
    school_id: Optional[uuid.UUID]  = None
    niveau:    Optional[List[str]]  = None
    classes:   Optional[List[str]]  = None

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, v: Optional[str]) -> Optional[str]:
        return _normalize_sn_phone(v)


# ── Réponse ───────────────────────────────────────────────────────────────────

class SchoolBrief(BaseModel):
    id:     uuid.UUID
    name:   str
    region: Optional[str] = None
    model_config = {"from_attributes": True}


class UserResponse(BaseModel):
    id:                  uuid.UUID
    name:                str
    title:               Optional[str]
    email:               Optional[str]
    phone:               Optional[str]
    role:                UserRole
    status:              UserStatus
    school_id:           Optional[uuid.UUID]
    niveau:              Optional[List[str]]
    classes:             Optional[List[str]]
    must_change_password: bool
    school:              Optional[SchoolBrief] = None

    model_config = {"from_attributes": True}


# ── Pagination ────────────────────────────────────────────────────────────────

class UserList(BaseModel):
    total: int
    items: List[UserResponse]
