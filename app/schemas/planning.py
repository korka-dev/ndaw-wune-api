import uuid
from datetime import time
from typing import List, Literal, Optional
from pydantic import BaseModel, field_validator, model_validator


class PlanningSegmentCreate(BaseModel):
    session_id:  uuid.UUID
    teacher_id:  Optional[uuid.UUID] = None
    school_id:   Optional[uuid.UUID] = None
    semaine:     Optional[int] = None
    jour:        int
    heure_debut: time
    heure_fin:   time
    matiere:     Optional[str] = None
    classe:      Optional[str] = None
    # Lot L9 : None = déduit de la matière (« Français langue seconde », « FLS »).
    reserve_fls: Optional[bool] = None
    # Lot L11 : tous | fls | sans_fls ; None = « fls » si réservé FLS, sinon « tous ».
    type_ecole: Optional[Literal["tous", "fls", "sans_fls"]] = None

    @field_validator("jour")
    @classmethod
    def jour_valide(cls, v: int) -> int:
        if v not in range(7):
            raise ValueError("Le jour doit être compris entre 0 (Lundi) et 6 (Dimanche).")
        return v

    @model_validator(mode="after")
    def heures_coherentes(self):
        if self.heure_fin <= self.heure_debut:
            raise ValueError("L'heure de fin doit être postérieure à l'heure de début.")
        return self


class PlanningSegmentUpdate(BaseModel):
    semaine:     Optional[int]  = None
    jour:        Optional[int]  = None
    heure_debut: Optional[time] = None
    heure_fin:   Optional[time] = None
    matiere:     Optional[str]  = None
    school_id:   Optional[uuid.UUID] = None
    classe:      Optional[str]  = None
    reserve_fls: Optional[bool] = None
    type_ecole:  Optional[Literal["tous", "fls", "sans_fls"]] = None


class PlanningSegmentResponse(BaseModel):
    id:          uuid.UUID
    session_id:  uuid.UUID
    teacher_id:  Optional[uuid.UUID] = None
    school_id:   Optional[uuid.UUID] = None
    semaine:     Optional[int] = None
    jour:        int
    heure_debut: time
    heure_fin:   time
    matiere:     Optional[str] = None
    classe:      Optional[str] = None
    reserve_fls: bool = False
    type_ecole:  str = "tous"
    teacher_name: Optional[str] = None

    model_config = {"from_attributes": True}


class PlanningList(BaseModel):
    total: int
    items: List[PlanningSegmentResponse]
