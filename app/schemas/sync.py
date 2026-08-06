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
    status:    Optional[str] = None
    school_id: Optional[uuid.UUID]
    niveau:    Optional[List[str]] = None
    classes:   Optional[List[str]]
    groupe_recherche: Optional[str] = None
    app_access: str = "full"

    model_config = {"from_attributes": True}


class SyncSchool(BaseModel):
    id:             uuid.UUID
    name:           str
    code_ecole:     Optional[int] = None
    region:         Optional[str]
    city:           Optional[str]
    director:       Optional[str]
    director_phone: Optional[str] = None
    langue:         Optional[str] = None

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
    semaine:     Optional[int] = None
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
    statut_selection: Optional[str] = None   # "Titulaire" | "Remplaçant" | None (Base NWV 2026 / RCT)

    model_config = {"from_attributes": True}


class SyncRapportQuestion(BaseModel):
    id:       uuid.UUID
    label:    str
    type:     str
    options:  Optional[List[str]] = None
    required: bool
    ordre:    int

    model_config = {"from_attributes": True}


class SyncRapportDifficulte(BaseModel):
    id:    uuid.UUID
    label: str
    ordre: int

    model_config = {"from_attributes": True}


class SyncStats(BaseModel):
    """Compteurs affichés sur la page profil de l'enseignant."""
    nb_eleves: int = 0
    nb_tests:  int = 0   # évaluations enregistrées pour ses élèves
    nb_fiches: int = 0   # ressources documentaires disponibles pour sa langue


class SyncPayload(BaseModel):
    """Payload complet téléchargé par l'app mobile pour fonctionner hors-ligne."""
    synced_at:        datetime
    profile:          SyncProfile
    school:           Optional[SyncSchool]
    active_session:   Optional[SyncSession]
    planning:         List[SyncPlanningSegment]
    eleves:           List[SyncEleve] = []
    # Élèves "Remplaçant" (Base NWV 2026 / RCT) des classes du tuteur — pas
    # affichés dans les listes courantes, seulement comme réservoir pour le
    # bouton « Remplacer un élève » (RemplacementSheet côté app).
    remplacants:      List[SyncEleve] = []
    rapport_questions:  List[SyncRapportQuestion]  = []
    rapport_difficultes: List[SyncRapportDifficulte] = []
    rapport_libelles:   dict[str, str] = {}
    nb_semaines: int = 10
    nb_jours:    int = 3
    stats:       SyncStats = SyncStats()
