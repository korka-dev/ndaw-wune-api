"""Endpoint App mobile — Enregistrement de l'utilisation des fonctionnalités.

L'app envoie (en lot, best-effort) les fonctionnalités ouvertes par
l'utilisateur afin que l'admin sache lesquelles sont les plus utilisées.

Route :
  POST /app/usage → enregistre un lot d'événements d'utilisation
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from typing import Optional

from fastapi import APIRouter, status
from pydantic import BaseModel

from app.core.deps import DB, MobileUser
from app.models.usage_log import UsageLog

router = APIRouter(prefix="/usage", tags=["App — Utilisation"])

# Fonctionnalités reconnues (évite de stocker des valeurs arbitraires).
# "timer" est conservé pour compatibilité avec les anciennes versions de
# l'app mobile qui n'envoient pas encore les 4 actions détaillées ci-dessous.
ALLOWED_FEATURES = {
    "accueil", "planning", "timer", "rapports", "rapport_journalier",
    "ressources", "evaluations", "presences", "difficultes", "remarques",
    "profil",
    "timer_start", "timer_pause", "timer_resume", "timer_stop",
}


class UsageEventIn(BaseModel):
    feature: str
    # Horodatage côté client (optionnel — utile pour les envois différés offline)
    at: str | None = None
    # Renseignés pour les événements timer_* : relient le log à la séance
    # concernée et, sur timer_stop, à la durée calculée par l'app (secondes).
    seance_id: Optional[str] = None
    duration_seconds: Optional[int] = None


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

        seance_uuid: uuid.UUID | None = None
        if ev.seance_id:
            try:
                seance_uuid = uuid.UUID(ev.seance_id)
            except ValueError:
                seance_uuid = None

        db.add(UsageLog(
            id=uuid.uuid4(),
            user_id=current_user.id,
            user_name=current_user.name,
            user_role=current_user.role.value,
            user_code=current_user.phone,
            feature=feature,
            seance_id=seance_uuid,
            duration_seconds=ev.duration_seconds if feature == "timer_stop" else None,
            created_at=created_at,
            updated_at=created_at,
        ))
        recorded += 1

    await db.commit()
    return {"recorded": recorded}
