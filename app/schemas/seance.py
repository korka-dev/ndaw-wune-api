import uuid
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field, field_validator
from app.models.seance import SeanceStatus


# ── Pause event ───────────────────────────────────────────────────────────────

class PauseEvent(BaseModel):
    """Un événement pause/reprise dans une séance."""
    paused_at:   str            # ISO 8601
    resumed_at:  Optional[str] = None  # None si la pause n'est pas encore levée


# ── Seance ────────────────────────────────────────────────────────────────────

class SeanceStart(BaseModel):
    """Payload envoyé quand l'enseignant démarre le timer."""
    session_id:           uuid.UUID
    planning_segment_id:  Optional[uuid.UUID] = None
    classe:               str
    matiere:              Optional[str]        = None
    date_seance:          datetime
    started_at:           datetime
    nb_eleves_total:      Optional[int]        = None
    # Identifiant stable généré par le téléphone (APP-16/18). Facultatif pour
    # les anciennes versions de l'app.
    client_seance_id:     Optional[str]        = Field(default=None, max_length=64)

    @field_validator("classe")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("La classe ne peut pas être vide.")
        return v.strip()


class SeancePauseBody(BaseModel):
    """Payload envoyé quand l'enseignant met en pause."""
    paused_at: datetime


class SeanceResumeBody(BaseModel):
    """Payload envoyé quand l'enseignant reprend après une pause."""
    resumed_at: datetime


class SeanceFinish(BaseModel):
    """Payload envoyé quand l'enseignant stoppe le timer."""
    finished_at:          datetime
    duree_minutes:        int
    nb_eleves_presents:   Optional[int]       = None
    # Pauses transmises pour réconciliation offline (optionnel)
    pauses:               List[PauseEvent]    = []
    total_paused_minutes: Optional[int]       = None


class SeanceComplete(SeanceStart):
    """Séance faite hors ligne, envoyée en une fois (démarrage + clôture).

    Idempotente par `client_seance_id` (obligatoire) et indépendante de la
    séance éventuellement active sur le serveur (APP-16/18).
    """
    client_seance_id:     str                  = Field(min_length=1, max_length=64)
    finished_at:          datetime
    duree_minutes:        int                  = Field(ge=0, le=24 * 60)
    nb_eleves_presents:   Optional[int]        = None
    pauses:               List[PauseEvent]     = []
    total_paused_minutes: Optional[int]        = None


class SeanceRecovery(BaseModel):
    """Clôture d'une séance oubliée par le tuteur (APP-21). Aucune durée n'est
    inférée : la séance reste sans durée mesurée."""
    motif: Optional[str] = Field(default=None, max_length=500)


class SeanceResponse(BaseModel):
    id:                   uuid.UUID
    session_id:           uuid.UUID
    teacher_id:           uuid.UUID
    planning_segment_id:  Optional[uuid.UUID]
    classe:               str
    matiere:              Optional[str]
    date_seance:          datetime
    started_at:           Optional[datetime]
    finished_at:          Optional[datetime]
    duree_minutes:        Optional[int]
    nb_eleves_presents:   Optional[int]
    nb_eleves_total:      Optional[int]
    status:               SeanceStatus
    pauses:               List[PauseEvent]    = []
    total_paused_minutes: Optional[int]       = None
    client_seance_id:     Optional[str]       = None
    cloture_mode:         Optional[str]       = None
    auto_closed:          bool                = False

    model_config = {"from_attributes": True}


class SeanceList(BaseModel):
    total: int
    items: List[SeanceResponse]


# ── Séance manquée ────────────────────────────────────────────────────────────

class SeanceMissedReport(BaseModel):
    """Payload envoyé par le mobile quand un créneau planifié est manqué."""
    session_id:           uuid.UUID
    planning_segment_id:  Optional[uuid.UUID] = None
    classe:               str
    matiere:              Optional[str]        = None
    date_seance:          datetime             # date du jour
    heure_debut:          str                  # "HH:MM" — pour traçabilité
    heure_fin:            str                  # "HH:MM" — pour traçabilité

    @field_validator("classe")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("La classe ne peut pas être vide.")
        return v.strip()


# ── RapportProf ───────────────────────────────────────────────────────────────

class RapportCreate(BaseModel):
    seance_id:           uuid.UUID
    contenu:             str
    difficultes:         Optional[str] = None
    points_positifs:     Optional[str] = None
    soumis_en_offline:   bool          = False

    @field_validator("contenu")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Le contenu du rapport ne peut pas être vide.")
        return v.strip()


class RapportResponse(BaseModel):
    id:                  uuid.UUID
    seance_id:           uuid.UUID
    teacher_id:          uuid.UUID
    contenu:             str
    difficultes:         Optional[str]
    points_positifs:     Optional[str]
    soumis_en_offline:   bool
    created_at:          datetime

    model_config = {"from_attributes": True}


class RapportList(BaseModel):
    total: int
    items: List[RapportResponse]
