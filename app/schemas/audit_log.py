from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class AuditLogResponse(BaseModel):
    id:          uuid.UUID
    user_id:     Optional[uuid.UUID] = None
    user_name:   str
    user_role:   str
    action:      str
    entity:      str
    method:      str
    path:        str
    description: str
    created_at:  datetime

    model_config = {"from_attributes": True}
