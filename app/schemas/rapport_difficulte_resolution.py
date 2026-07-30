from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class DifficulteResolutionUpdate(BaseModel):
    difficulte_label: str
    resolue: bool
    commentaire_resolution: Optional[str] = None


class RapportDifficulteResolutionResponse(BaseModel):
    id:                      uuid.UUID
    rapport_id:              uuid.UUID
    difficulte_label:        str
    resolue:                 bool
    resolue_par:              Optional[uuid.UUID]
    resolue_le:               Optional[datetime]
    commentaire_resolution:   Optional[str]

    model_config = {"from_attributes": True}
