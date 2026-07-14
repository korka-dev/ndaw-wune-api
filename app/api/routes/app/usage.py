"""Endpoint App mobile — Enregistrement de l'utilisation des fonctionnalités.

L'app envoie (en lot, best-effort) les fonctionnalités ouvertes par
l'utilisateur afin que l'admin sache lesquelles sont les plus utilisées.

Route :
  POST /app/usage → enregistre un lot d'événements d'utilisation
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, status
from pydantic import BaseModel

from app.core.deps import DB, MobileUser
from app.models.usage_log import UsageLog

router = APIRouter(prefix="/usage", tags=["App — Utilisation"])

# Fonctionnalités reconnues (évite de stocker des valeurs arbitraires)
ALLOWED_FEATURES = {
    "accueil", "planning", "timer", "rapports", "rapport_journalier",
    "ressources", "evaluations", "presences", "difficultes", "remarques",
    "profil",
}


class UsageEventIn(BaseModel):
    feature: str
    # Horodatage côté client (optionnel — utile pour les envois différés offline)
    at: str | None = None


class UsageBatchIn(BaseModel):
    events: list[UsageEventIn]


@router.post("", status_code=status.HTTP_201_CREATED)
async def record_usage(body: UsageBatchIn, current_user: MobileUser, db: DB) -> dict:
    now = datetime.now(timezone.utc)
    recorded = 0
    for ev in body.events:
        feature = (ev.feature or "").strip().lower()
        if feature not in ALLOWED_FEATURES:
            continue
        created_at = now
        if ev.at:
            try:
                created_at = datetime.fromisoformat(ev.at.replace("Z", "+00:00"))
            except ValueError:
                created_at = now
        db.add(UsageLog(
            id=uuid.uuid4(),
            user_id=current_user.id,
            user_name=current_user.name,
            user_role=current_user.role.value,
            feature=feature,
            created_at=created_at,
            updated_at=created_at,
        ))
        recorded += 1

    await db.commit()
    return {"recorded": recorded}
