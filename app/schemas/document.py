from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class DocumentResponse(BaseModel):
    id: uuid.UUID
    title: str
    original_filename: str
    mime_type: str
    file_size: int
    description: str | None
    resource_type: str = "document"
    langue: str | None = None
    uploaded_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DocumentCreate(BaseModel):
    """Métadonnées optionnelles envoyées en même temps que le fichier."""
    title: str | None = None
    description: str | None = None


class DocumentUpdate(BaseModel):
    """Métadonnées modifiables après upload."""
    title: str | None = None
    description: str | None = None
    langue: str | None = None
