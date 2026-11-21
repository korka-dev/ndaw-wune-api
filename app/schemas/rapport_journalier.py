import json
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Optional, List
from pydantic import BaseModel, Field, field_validator, model_validator


DATE_RAPPORT_MIN = date(2024, 1, 1)
MAX_PHOTOS = 3


def _json(v: Optional[str], champ: str, attendu: type):
    """Contrôle qu'un champ « JSON sérialisé » contient bien le type attendu."""
    if v is None:
        return None
    try:
        val = json.loads(v)
    except (TypeError, ValueError):
        raise ValueError(f"{champ} : JSON invalide.")
    if not isinstance(val, attendu):
        raise ValueError(f"{champ} : {'liste' if attendu is list else 'objet'} JSON attendu.")
    return val


class RapportJournalierCreate(BaseModel):
    """Rapport journalier soumis par le mobile (rapport V2, APP-28).

    Bornes physiques ici (422 par champ) ; bornes de campagne (nombre de
    semaines / jours configurés) vérifiées par la route. Les champs « JSON
    sérialisé » restent des chaînes (compatibilité des files hors ligne) mais
    leur contenu est typé.
    """
    date_rapport:             date
    ief:                      str              = Field(max_length=255)
    commune:                  str              = Field(max_length=255)
    ecole:                    str              = Field(min_length=1, max_length=255)
    superviseur:              str              = Field(max_length=255)
    nom_tuteur:               str              = Field(min_length=1, max_length=255)
    # Bornes physiques : on rejette les valeurs impossibles (négatives, absurdes)
    nb_absences:              int              = Field(default=0, ge=0, le=200)
    absents:                  Optional[str]    = Field(default=None, max_length=10_000)  # noms séparés par des virgules
    semaine:                  int              = Field(ge=1, le=53)
    jour_cours:               int              = Field(ge=1, le=7)
    difficultes:              str              = Field(max_length=10_000)  # liste JSON
    autres_difficultes:       Optional[str]    = Field(default=None, max_length=5_000)
    description_difficultes:  Optional[str]    = Field(default=None, max_length=5_000)
    directeur_venu:           bool
    besoin_appui:             bool
    domaines_appui:           Optional[str]    = Field(default=None, max_length=5_000)
    has_observations:         bool             = False
    commentaires:             Optional[str]    = Field(default=None, max_length=10_000)
    soumis_en_offline:        bool             = True
    photo_classe_url:         Optional[str]    = None
    photos_classe_url:        Optional[str]    = None   # liste JSON de data URIs (max 3)
    reponses_questions:       Optional[str]    = Field(default=None, max_length=50_000)  # objet JSON
    # Clé d'opération générée par le téléphone à la saisie (APP-16).
    client_op_id:             Optional[str]    = Field(default=None, min_length=1, max_length=64)
    # Lot L4 (D3.3) : identifiants des élèves absents, pour une table
    # individuelle d'absences liée à l'inscription (les noms restent dans `absents`).
    absents_ids:              Optional[list[uuid.UUID]] = Field(default=None, max_length=200)
    # Lot L4 (B1) : horodatage de la saisie sur le téléphone (avec fuseau).
    submitted_at:             Optional[datetime] = None

    @field_validator("ecole", "nom_tuteur")
    @classmethod
    def _non_vide(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("ne peut pas être vide.")
        return v.strip()

    @field_validator("date_rapport")
    @classmethod
    def _date_plausible(cls, v: date) -> date:
        # +1 jour : fuseaux et horloges de téléphone légèrement en avance.
        if v > date.today() + timedelta(days=1):
            raise ValueError("la date du rapport ne peut pas être dans le futur.")
        if v < DATE_RAPPORT_MIN:
            raise ValueError(f"date antérieure au programme ({DATE_RAPPORT_MIN:%d/%m/%Y}).")
        return v

    @field_validator("difficultes")
    @classmethod
    def _difficultes(cls, v: str) -> str:
        _json(v, "difficultes", list)
        return v

    @field_validator("reponses_questions")
    @classmethod
    def _reponses(cls, v: Optional[str]) -> Optional[str]:
        _json(v, "reponses_questions", dict)
        return v

    @field_validator("photos_classe_url")
    @classmethod
    def _photos(cls, v: Optional[str]) -> Optional[str]:
        val = _json(v, "photos_classe_url", list)
        if val is not None:
            if len(val) > MAX_PHOTOS:
                raise ValueError(f"au plus {MAX_PHOTOS} photos.")
            if not all(isinstance(x, str) and x.startswith("data:image/") for x in val):
                raise ValueError("chaque photo doit être une image (data:image/...).")
        return v

    @field_validator("photo_classe_url")
    @classmethod
    def _photo(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.startswith(("data:image/", "https://")):
            raise ValueError("la photo doit être une image (data:image/... ou https://...).")
        return v

    @field_validator("absents_ids")
    @classmethod
    def _absents_ids(cls, v: Optional[list[uuid.UUID]]) -> Optional[list[uuid.UUID]]:
        if v is not None and len(set(v)) != len(v):
            raise ValueError("un même élève est déclaré absent deux fois.")
        return v or None

    @field_validator("submitted_at")
    @classmethod
    def _submitted_at(cls, v: Optional[datetime]) -> Optional[datetime]:
        if v is None:
            return v
        if v.tzinfo is None:
            raise ValueError("submitted_at doit porter un fuseau horaire (ISO 8601).")
        if v > datetime.now(timezone.utc) + timedelta(days=1):
            raise ValueError("submitted_at ne peut pas être dans le futur.")
        return v

    @model_validator(mode="after")
    def _coherence(self):
        # Valeur manquante distincte de zéro : pas de liste d'absents sans absence.
        if self.nb_absences == 0 and self.absents and self.absents.strip():
            raise ValueError("absents renseignés alors que nb_absences vaut 0.")
        if self.absents_ids and len(self.absents_ids) > self.nb_absences:
            raise ValueError("plus d'élèves absents identifiés que nb_absences.")
        if self.besoin_appui and not (self.domaines_appui or "").strip():
            raise ValueError("domaines_appui requis quand besoin_appui est vrai.")
        return self


class RapportJournalierAdminListItem(BaseModel):
    """Ligne de liste pour le dashboard admin — SANS les photos.

    Les photos sont stockées en base sous forme de data URI base64 : un seul
    rapport pèse couramment 1 à 3 Mo. Les renvoyer dans la liste faisait
    transiter plusieurs dizaines de Mo par page de 20. On expose seulement leur
    nombre ; les images se récupèrent une par une via
    GET /admin/rapports/journalier/{id}/photo/{index}.
    """
    id:                       uuid.UUID
    teacher_id:               uuid.UUID
    date_rapport:             date
    ief:                      str
    commune:                  str
    ecole:                    str
    superviseur:              str
    nom_tuteur:               str
    nb_absences:              int
    absents:                  Optional[str]
    semaine:                  int
    jour_cours:               int
    difficultes:              str
    autres_difficultes:       Optional[str]
    description_difficultes:  Optional[str]
    directeur_venu:           bool
    besoin_appui:             bool
    domaines_appui:           Optional[str]
    has_observations:         bool
    commentaires:             Optional[str]
    soumis_en_offline:        bool
    reponses_questions:       Optional[str] = None
    nb_photos:                int = 0
    created_at:               datetime
    # Cycle de vie (APP-31) et campagne (APP-22)
    statut:                   str = "soumis"
    version:                  int = 1
    statut_motif:             Optional[str] = None
    statut_at:                Optional[datetime] = None
    session_id:               Optional[uuid.UUID] = None

    model_config = {"from_attributes": True}


# Même forme pour l'app mobile : listes et accusés de réception sans photos
# (APP-37). Les images se chargent via GET /app/rapports/journalier/{id}/photo/{i}.
RapportJournalierListItem = RapportJournalierAdminListItem


class RapportJournalierResponse(BaseModel):
    id:                       uuid.UUID
    teacher_id:               uuid.UUID
    date_rapport:             date
    ief:                      str
    commune:                  str
    ecole:                    str
    superviseur:              str
    nom_tuteur:               str
    nb_absences:              int
    absents:                  Optional[str]
    semaine:                  int
    jour_cours:               int
    difficultes:              str
    autres_difficultes:       Optional[str]
    description_difficultes:  Optional[str]
    directeur_venu:           bool
    besoin_appui:             bool
    domaines_appui:           Optional[str]
    has_observations:         bool
    commentaires:             Optional[str]
    soumis_en_offline:        bool
    photo_classe_url:         Optional[str]
    photos_classe_url:        Optional[str] = None
    reponses_questions:       Optional[str] = None
    created_at:               datetime

    model_config = {"from_attributes": True}


class RapportAnnulation(BaseModel):
    """Annulation motivée d'un rapport (APP-31) : le rapport reste en base."""
    motif: str = Field(min_length=3, max_length=1_000)

    @field_validator("motif")
    @classmethod
    def _motif(cls, v: str) -> str:
        if len(v.strip()) < 3:
            raise ValueError("motif obligatoire (3 caractères au moins).")
        return v.strip()


# Champs corrigeables par l'auteur ; les photos, la date et l'école ne se
# corrigent pas (créer un nouveau rapport et annuler l'ancien).
CHAMPS_CORRIGEABLES = (
    "nb_absences", "absents", "semaine", "jour_cours", "difficultes", "autres_difficultes",
    "description_difficultes", "directeur_venu", "besoin_appui", "domaines_appui",
    "has_observations", "commentaires", "reponses_questions",
)


class RapportCorrection(RapportAnnulation):
    """Correction motivée : nouvelle version, l'ancienne est conservée."""
    nb_absences:             Optional[int]  = Field(default=None, ge=0, le=200)
    absents:                 Optional[str]  = Field(default=None, max_length=10_000)
    semaine:                 Optional[int]  = Field(default=None, ge=1, le=53)
    jour_cours:              Optional[int]  = Field(default=None, ge=1, le=7)
    difficultes:             Optional[str]  = Field(default=None, max_length=10_000)
    autres_difficultes:      Optional[str]  = Field(default=None, max_length=5_000)
    description_difficultes: Optional[str]  = Field(default=None, max_length=5_000)
    directeur_venu:          Optional[bool] = None
    besoin_appui:            Optional[bool] = None
    domaines_appui:          Optional[str]  = Field(default=None, max_length=5_000)
    has_observations:        Optional[bool] = None
    commentaires:            Optional[str]  = Field(default=None, max_length=10_000)
    reponses_questions:      Optional[str]  = Field(default=None, max_length=50_000)

    @field_validator("difficultes")
    @classmethod
    def _difficultes(cls, v):
        _json(v, "difficultes", list)
        return v

    @field_validator("reponses_questions")
    @classmethod
    def _reponses(cls, v):
        _json(v, "reponses_questions", dict)
        return v


class RapportVersionItem(BaseModel):
    version:    int
    statut:     str
    action:     str
    motif:      str
    auteur_id:  Optional[uuid.UUID]
    created_at: datetime
    contenu:    dict

    model_config = {"from_attributes": True}
