"""Schémas — visites pédagogiques, grilles et demandes d'appui (lot L6)."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.demande_appui import ORIGINES_DEMANDE, STATUTS_DEMANDE
from app.models.visite import TYPES_ITEM


# ── Grilles ───────────────────────────────────────────────────────────────────

class GrilleItemIn(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    domaine: str = Field(min_length=1, max_length=50)
    libelle: str = Field(min_length=1, max_length=500)
    type: str = "echelle"
    bareme_max: Optional[int] = Field(default=None, ge=1, le=100)
    ordre: int = 0
    aide: Optional[str] = Field(default=None, max_length=2000)
    obligatoire: bool = False

    @model_validator(mode="after")
    def _coherence(self):
        if self.type not in TYPES_ITEM:
            raise ValueError(f"type inconnu : {self.type} (attendu : {', '.join(TYPES_ITEM)})")
        if self.type == "echelle" and not self.bareme_max:
            raise ValueError(f"{self.code} : un item « echelle » exige un barème maximal.")
        if self.type != "echelle":
            self.bareme_max = 1 if self.type == "oui_non" else None
        return self


class GrilleItemOut(GrilleItemIn):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID


class GrilleIn(BaseModel):
    nom: str = Field(min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=5000)
    session_id: Optional[uuid.UUID] = None
    items: list[GrilleItemIn] = Field(default_factory=list, max_length=200)

    @field_validator("items")
    @classmethod
    def _codes_uniques(cls, v: list[GrilleItemIn]):
        codes = [i.code for i in v]
        doublons = sorted({c for c in codes if codes.count(c) > 1})
        if doublons:
            raise ValueError(f"codes d'item en double : {', '.join(doublons)}")
        return v


class GrilleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    nom: str
    version: int
    statut: str
    session_id: Optional[uuid.UUID]
    description: Optional[str]
    publiee_le: Optional[datetime]
    created_at: datetime
    items: list[GrilleItemOut] = []
    nb_visites: int = 0


# ── Visites ───────────────────────────────────────────────────────────────────

class ReponseIn(BaseModel):
    item_id: uuid.UUID
    valeur: Optional[float] = None
    disponible: Optional[bool] = None
    texte: Optional[str] = Field(default=None, max_length=5000)
    non_applicable: bool = False


class RecommandationIn(BaseModel):
    titre: str = Field(min_length=1, max_length=300)
    description: Optional[str] = Field(default=None, max_length=5000)
    domaine: Optional[str] = Field(default=None, max_length=200)
    echeance: Optional[date] = None


class VisiteIn(BaseModel):
    teacher_id: uuid.UUID
    grille_id: uuid.UUID
    date_visite: date
    classe: Optional[str] = Field(default=None, max_length=50)
    heure_debut: Optional[str] = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    duree_minutes: Optional[int] = Field(default=None, ge=1, le=600)
    points_appui: Optional[str] = Field(default=None, max_length=10000)
    points_amelioration: Optional[str] = Field(default=None, max_length=10000)
    commentaire: Optional[str] = Field(default=None, max_length=10000)
    reponses: list[ReponseIn] = Field(default_factory=list, max_length=300)
    # Recommandations transformées en demandes d'accompagnement (D9.3).
    recommandations: list[RecommandationIn] = Field(default_factory=list, max_length=20)
    client_op_id: Optional[str] = Field(default=None, min_length=1, max_length=64)
    submitted_at: Optional[datetime] = None

    @field_validator("date_visite")
    @classmethod
    def _date_plausible(cls, v: date):
        if v < date(2024, 1, 1) or v > date.today() + timedelta(days=1):
            raise ValueError("date de visite hors de la période plausible.")
        return v


class ReponseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    item_id: uuid.UUID
    valeur: Optional[float]
    disponible: Optional[bool]
    texte: Optional[str]
    non_applicable: bool


class VisiteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    superviseur_id: uuid.UUID
    superviseur_nom: Optional[str] = None
    teacher_id: uuid.UUID
    teacher_nom: Optional[str] = None
    school_id: Optional[uuid.UUID]
    ecole: Optional[str] = None
    classe: Optional[str]
    session_id: Optional[uuid.UUID]
    grille_id: uuid.UUID
    grille_version: Optional[int] = None
    date_visite: date
    heure_debut: Optional[str]
    duree_minutes: Optional[int]
    points_appui: Optional[str]
    points_amelioration: Optional[str]
    commentaire: Optional[str]
    score_obtenu: Optional[float]
    score_max: Optional[float]
    scores_domaines: Optional[dict]
    nb_items_manquants: int
    nb_outils_indisponibles: int
    statut: str
    statut_motif: Optional[str]
    submitted_at: Optional[datetime]
    created_at: datetime
    reponses: list[ReponseOut] = []
    demandes: list["DemandeOut"] = []


class MotifIn(BaseModel):
    motif: str = Field(min_length=3, max_length=2000)


# ── Demandes d'appui ──────────────────────────────────────────────────────────

class DemandeIn(BaseModel):
    titre: str = Field(min_length=1, max_length=300)
    description: Optional[str] = Field(default=None, max_length=5000)
    domaine: Optional[str] = Field(default=None, max_length=200)
    beneficiaire_id: Optional[uuid.UUID] = None
    responsable_id: Optional[uuid.UUID] = None
    echeance: Optional[date] = None


class DemandeDepuisRapportIn(BaseModel):
    rapport_id: uuid.UUID
    type: str = Field(pattern="^(difficulte|besoin)$")
    libelle: str = Field(min_length=1, max_length=500)
    responsable_id: Optional[uuid.UUID] = None
    echeance: Optional[date] = None
    description: Optional[str] = Field(default=None, max_length=5000)


class DemandeAction(BaseModel):
    """Une étape du circuit : statut, affectation, échéance et/ou commentaire."""
    statut: Optional[str] = None
    responsable_id: Optional[uuid.UUID] = None
    retirer_responsable: bool = False
    echeance: Optional[date] = None
    commentaire: Optional[str] = Field(default=None, max_length=5000)

    @field_validator("statut")
    @classmethod
    def _statut(cls, v):
        if v is not None and v not in STATUTS_DEMANDE:
            raise ValueError(f"statut inconnu : {v}")
        return v


class EvenementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    type: str
    ancien_statut: Optional[str]
    nouveau_statut: Optional[str]
    responsable_id: Optional[uuid.UUID]
    responsable_nom: Optional[str] = None
    commentaire: Optional[str]
    auteur_id: Optional[uuid.UUID]
    auteur_nom: Optional[str] = None
    created_at: datetime


class DemandeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    origine: str
    visite_id: Optional[uuid.UUID]
    rapport_id: Optional[uuid.UUID]
    libelle_source: Optional[str]
    domaine: Optional[str]
    titre: str
    description: Optional[str]
    beneficiaire_id: Optional[uuid.UUID]
    beneficiaire_nom: Optional[str] = None
    school_id: Optional[uuid.UUID]
    ecole: Optional[str] = None
    session_id: Optional[uuid.UUID]
    responsable_id: Optional[uuid.UUID]
    responsable_nom: Optional[str] = None
    echeance: Optional[date]
    en_retard: bool = False
    statut: str
    cree_par_id: Optional[uuid.UUID]
    cloturee_le: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    evenements: list[EvenementOut] = []


VisiteOut.model_rebuild()

__all__ = ["ORIGINES_DEMANDE"]
