"""Schéma du payload de synchronisation offline envoyé à l'application mobile."""
import uuid
from datetime import datetime, date, time
from typing import Optional, List
from pydantic import BaseModel, model_validator


class SyncProfile(BaseModel):
    id:        uuid.UUID
    name:      str
    title:     Optional[str]
    email:     Optional[str]
    phone:     Optional[str]
    role:      str
    school_id: Optional[uuid.UUID]
    classes:   Optional[List[str]]

    model_config = {"from_attributes": True}


class SyncSchool(BaseModel):
    id:       uuid.UUID
    name:     str
    region:   Optional[str]
    city:     Optional[str]
    director: Optional[str]

    model_config = {"from_attributes": True}


class SyncSession(BaseModel):
    id:          uuid.UUID
    name:        str
    date_debut:  date
    date_fin:    date
    status:      str
    description: Optional[str]

    model_config = {"from_attributes": True}


class SyncPlanningSegment(BaseModel):
    id:          uuid.UUID
    jour:        int
    heure_debut: time
    heure_fin:   time
    classe:      Optional[str] = None
    matiere:     Optional[str] = None
    titre:       Optional[str] = None   # dérivé : matiere ?? classe

    model_config = {"from_attributes": True}

    @model_validator(mode="after")
    def derive_titre(self) -> "SyncPlanningSegment":
        """Calcule titre depuis matiere ou classe si non fourni."""
        if not self.titre:
            self.titre = self.matiere or self.classe or ""
        return self


class SyncEleve(BaseModel):
    id:     uuid.UUID
    nom:    str
    prenom: Optional[str]
    classe: str

    model_config = {"from_attributes": True}


class SyncRapportQuestion(BaseModel):
    id:       uuid.UUID
    label:    str
    type:     str
    options:  Optional[List[str]] = None
    required: bool
    ordre:    int

    model_config = {"from_attributes": True}


class SyncPayload(BaseModel):
    """Payload complet téléchargé par l'app mobile pour fonctionner hors-ligne."""
    synced_at:        datetime
    profile:          SyncProfile
    school:           Optional[SyncSchool]
    active_session:   Optional[SyncSession]
    planning:         List[SyncPlanningSegment]
    eleves:           List[SyncEleve] = []
    rapport_questions: List[SyncRapportQuestion] = []
