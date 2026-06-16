from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, field_validator


def _normalize_phone(raw: str) -> str:
    """
    Réduit un numéro de téléphone sénégalais à ses 9 chiffres locaux.
    Exemples acceptés : +221770000000 | 00221770000000 | 221770000000
                        77 000 00 00  | 770000000
    """
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 12 and digits.startswith("221"):
        digits = digits[3:]
    elif len(digits) == 14 and digits.startswith("00221"):
        digits = digits[5:]
    return digits


class LoginRequest(BaseModel):
    identifier: str    # e-mail OU numéro de téléphone
    password:   str

    @field_validator("identifier")
    @classmethod
    def normalize_identifier(cls, v: str) -> str:
        v = v.strip()
        if "@" in v:
            return v.lower()
        # Numéro de téléphone → normalisation vers 9 chiffres locaux
        return _normalize_phone(v)

    @field_validator("password")
    @classmethod
    def password_not_empty(cls, v: str) -> str:
        if not v:
            raise ValueError("Le mot de passe ne peut pas être vide.")
        return v


class TokenResponse(BaseModel):
    access_token:        str
    refresh_token:       str
    token_type:          str  = "bearer"
    must_change_password: bool = False   # signal au client pour rediriger


class RefreshRequest(BaseModel):
    refresh_token: str


class MeResponse(BaseModel):
    id:                  str
    name:                str
    email:               Optional[str]
    phone:               Optional[str]
    role:                str
    status:              str
    title:               Optional[str]
    school_id:           Optional[str]
    must_change_password: bool = False

    model_config = {"from_attributes": True}

    @field_validator("id", "school_id", mode="before")
    @classmethod
    def uuid_to_str(cls, v: object) -> Optional[str]:
        return str(v) if v is not None else None


class ChangePasswordRequest(BaseModel):
    new_password:     str
    confirm_password: str = ""   # optionnel : ignoré si vide

    @field_validator("new_password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if not v:
            raise ValueError("Le mot de passe ne peut pas être vide.")
        return v

    @field_validator("confirm_password", mode="after")
    @classmethod
    def passwords_match(cls, v: str, info) -> str:
        if v and "new_password" in info.data and v != info.data["new_password"]:
            raise ValueError("Les mots de passe ne correspondent pas.")
        return v


class ResetPasswordRequest(BaseModel):
    """Réinitialisation de mot de passe sans authentification (écran 'Mot de passe oublié')."""
    identifier:       str   # e-mail ou téléphone
    new_password:     str
    confirm_password: str

    @field_validator("identifier")
    @classmethod
    def normalize_identifier(cls, v: str) -> str:
        v = v.strip()
        if "@" in v:
            return v.lower()
        return _normalize_phone(v)

    @field_validator("new_password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if not v:
            raise ValueError("Le mot de passe ne peut pas être vide.")
        return v

    @field_validator("confirm_password", mode="after")
    @classmethod
    def passwords_match(cls, v: str, info) -> str:
        if "new_password" in info.data and v != info.data["new_password"]:
            raise ValueError("Les mots de passe ne correspondent pas.")
        return v
