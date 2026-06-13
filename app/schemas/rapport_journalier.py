import uuid
from datetime import date, datetime
from typing import Optional, List
from pydantic import BaseModel, field_validator


class RapportJournalierCreate(BaseModel):
    date_rapport:             date
    ief:                      str
    commune:                  str
    ecole:                    str
    superviseur:              str
    nom_tuteur:               str
    nb_absences:              int              = 0
    absents:                  Optional[str]    = None   # JSON sérialisé
    semaine:                  int
    jour_cours:               int
    difficultes:              str                        # JSON sérialisé
    autres_difficultes:       Optional[str]    = None
    description_difficultes:  Optional[str]    = None
    directeur_venu:           bool
    besoin_appui:             bool
    domaines_appui:           Optional[str]    = None   # JSON sérialisé
    has_observations:         bool             = False
    commentaires:             Optional[str]    = None
    soumis_en_offline:        bool             = True
    photo_classe_url:         Optional[str]    = None
    photos_classe_url:        Optional[str]    = None   # JSON sérialisé (liste de data URIs, max 3)
    reponses_questions:       Optional[str]    = None   # JSON sérialisé {question_id: réponse}


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
