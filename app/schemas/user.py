from __future__ import annotations
from datetime import datetime

import re
import uuid
from typing import List, Optional

from pydantic import Field, BaseModel, field_validator, model_validator

from app.core.identifiers import normalize_email

from app.models.user import UserGroupe, UserRole, UserStatus


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
    # applique DEFAULT_USER_PASSWORD (configuration) et force le changement.
    password:  Optional[str]       = None
    role:      UserRole
    school_id: Optional[uuid.UUID] = None
    niveau:    Optional[List[str]] = None
    classes:   Optional[List[str]] = None
    # Accès app mobile : "full" (toutes fonctionnalités) ou "timer_only" (Timer uniquement)
    app_access: Optional[str]      = None
    # Groupe de l'étude RCT (enseignants uniquement) : "traitement" (minuteur) ou "controle" (sans)
    groupe_recherche: Optional[UserGroupe] = None

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

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v: Optional[str]) -> Optional[str]:
        # APP-08 : même politique minimale que le changement et le reset.
        if v is None or not v.strip():
            return None
        if len(v.strip()) < 8:
            raise ValueError("Le mot de passe doit contenir au moins 8 caractères.")
        return v.strip()

    @model_validator(mode="after")
    def email_or_phone_required(self) -> "UserCreate":
        if not self.email and not self.phone:
            raise ValueError("Un e-mail ou un numéro de téléphone est requis.")
        return self

    @field_validator("email", mode="before")
    @classmethod
    def _email(cls, v):
        return normalize_email(v)


# ── Mise à jour ───────────────────────────────────────────────────────────────

class UserUpdate(BaseModel):
    name:      Optional[str]        = None
    title:     Optional[str]        = None
    email:     Optional[str]        = None
    phone:     Optional[str]        = None
    # Pas de champ password (APP-02) : un mot de passe ne se change jamais par la
    # modification générale d'un compte, mais par /reset-password (politique,
    # invalidation des sessions) ou par /auth/change-password.
    status:    Optional[UserStatus] = None
    school_id: Optional[uuid.UUID]  = None
    niveau:    Optional[List[str]]  = None
    classes:   Optional[List[str]]  = None
    app_access: Optional[str]       = None
    groupe_recherche: Optional[UserGroupe] = None
    # APP-33 : motif obligatoire pour une dérogation au protocole de recherche.
    motif_protocole: Optional[str] = Field(default=None, max_length=1000)

    @field_validator("app_access")
    @classmethod
    def _acces(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("full", "timer_only", "no_timer"):
            raise ValueError("app_access : full, timer_only ou no_timer.")
        return v

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, v: Optional[str]) -> Optional[str]:
        return _normalize_sn_phone(v)

    @field_validator("email", mode="before")
    @classmethod
    def _email(cls, v):
        return normalize_email(v)


# ── Réinitialisation de mot de passe (admin) ──────────────────────────────────

class SetPasswordRequest(BaseModel):
    """Définit un NOUVEAU mot de passe pour un compte, depuis le dashboard admin.

    Il ne s'agit pas de « récupérer » le mot de passe existant : les mots de
    passe sont hachés (bcrypt, à sens unique — voir app/core/security.py) et ne
    sont récupérables sous aucune forme, ici comme ailleurs dans l'application.
    L'admin choisit une nouvelle valeur, communiquée ensuite au tuteur ou au
    superviseur (par oral, par SMS…).
    """
    new_password: str
    # Si True, l'utilisateur devra en choisir un autre à sa prochaine connexion
    # (même mécanisme qu'un compte nouvellement créé). Si False (défaut), le
    # mot de passe défini ici fonctionne directement — utile quand l'admin le
    # communique en personne et veut être sûr qu'il marche du premier coup.
    force_change: bool = False

    @field_validator("new_password")
    @classmethod
    def min_length(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 8:
            raise ValueError("Le mot de passe doit contenir au moins 8 caractères.")
        return v


# ── Réponse ───────────────────────────────────────────────────────────────────

class SchoolBrief(BaseModel):
    id:         uuid.UUID
    name:       str
    region:     Optional[str] = None
    code_ecole: Optional[int] = None
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
    app_access:          str = "full"
    groupe_recherche:    Optional[UserGroupe] = None
    must_change_password: bool
    school:              Optional[SchoolBrief] = None

    archived_at: Optional[datetime] = None  # APP-15
    model_config = {"from_attributes": True}


# ── Pagination ────────────────────────────────────────────────────────────────

class UserList(BaseModel):
    total: int
    items: List[UserResponse]
