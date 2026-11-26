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
    # APP-33 : accès effectif calculé par le serveur (protocole de recherche).
    minuteur_visible: bool = True
    # APP-14 : durée de conservation sur le téléphone des saisies déjà envoyées (None = pas de purge).
    retention_saisies_jours: Optional[int] = None
    acces_source: str = "reglage"

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
    fls:            Optional[bool] = None  # lot L9

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
    reserve_fls: bool = False           # lot L9 (informatif : déjà filtré par le serveur)
    type_ecole:  str = "tous"           # lot L11 (informatif : déjà filtré par le serveur)

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
    # RET07 : groupes pédagogiques affichés au tuteur (lecture seule côté app).
    groupe_lecture: Optional[str] = None
    groupe_maths:   Optional[str] = None

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


class ParametresTerrain(BaseModel):
    """Règles terrain fixées par ARED (lot L7) ; défauts = comportement existant."""
    remplacement_validation: str = "immediat"
    photo_rapport_obligatoire: bool = True
    minuteur_prealerte_secondes: Optional[int] = None
    minuteur_son: bool = True
    proposition_groupe_active: bool = True
    support_contact: Optional[str] = None
    systemes_supportes: Optional[str] = None
    rappels_max_par_jour: Optional[int] = None
    latest_app_version: Optional[str] = None


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
    parametres_terrain: ParametresTerrain = ParametresTerrain()
