"""Endpoint App mobile — Pointage de présence des enseignants par le superviseur.

Permet à un superviseur de terrain d'enregistrer (et de retrouver après
déconnexion/reconnexion) le pointage de présence du jour pour les enseignants
qui lui sont assignés.

Routes :
  GET  /app/supervisor/presences   → pointage déjà enregistré pour une date donnée
  POST /app/supervisor/presences   → enregistrer/valider le pointage du jour (batch)
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from app.core.deps import DB, SuperviseurUser
from app.models.supervisor_presence import SupervisorPresenceCheck

router = APIRouter(prefix="/supervisor", tags=["App — Superviseur"])


# ── Schémas ───────────────────────────────────────────────────────────────────

class PresenceEntryIn(BaseModel):
    teacher_id: str
    present:    bool
    motif:      Optional[str] = None


class PresenceCheckIn(BaseModel):
    date_jour: str       # ISO YYYY-MM-DD
    entries:   list[PresenceEntryIn]


class PresenceEntryOut(BaseModel):
    teacher_id: str
    present:    bool
    motif:      Optional[str] = None


class PresenceCheckOut(BaseModel):
    date_jour: str
    entries:   list[PresenceEntryOut]


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/presences", response_model=PresenceCheckOut)
async def get_presence_check(
    current_user: SuperviseurUser,
    db: DB,
    date_jour: Optional[str] = None,
) -> PresenceCheckOut:
    """
    Retourne le pointage déjà enregistré par ce superviseur pour la date donnée
    (par défaut aujourd'hui). Permet de réafficher l'état après reconnexion.
    """
    try:
        target_date = date.fromisoformat(date_jour) if date_jour else date.today()
    except ValueError:
        raise HTTPException(status_code=422, detail="date_jour invalide (format YYYY-MM-DD).")

    rows = (await db.execute(
        select(SupervisorPresenceCheck).where(
            SupervisorPresenceCheck.superviseur_id == current_user.id,
            SupervisorPresenceCheck.date_jour      == target_date,
        )
    )).scalars().all()

    return PresenceCheckOut(
        date_jour=target_date.isoformat(),
        entries=[
            PresenceEntryOut(teacher_id=str(r.teacher_id), present=r.present, motif=r.motif)
            for r in rows
        ],
    )


@router.post("/presences", status_code=status.HTTP_201_CREATED)
async def submit_presence_check(
    body: PresenceCheckIn,
    current_user: SuperviseurUser,
    db: DB,
) -> dict:
    """
    Enregistre/valide le pointage de présence du jour (batch).
    Upsert : si un pointage (superviseur, enseignant, date) existe déjà,
    il est mis à jour (présence + motif).
    """
    if not body.entries:
        raise HTTPException(status_code=422, detail="La liste de pointages est vide.")

    try:
        target_date = date.fromisoformat(body.date_jour)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"date_jour invalide : {body.date_jour}")

    now = datetime.now(timezone.utc)
    created = updated = 0

    for entry in body.entries:
        try:
            teacher_uuid = uuid.UUID(entry.teacher_id)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"teacher_id invalide : {entry.teacher_id}")

        existing = (await db.execute(
            select(SupervisorPresenceCheck).where(
                SupervisorPresenceCheck.superviseur_id == current_user.id,
                SupervisorPresenceCheck.teacher_id     == teacher_uuid,
                SupervisorPresenceCheck.date_jour      == target_date,
            )
        )).scalar_one_or_none()

        if existing:
            existing.present   = entry.present
            existing.motif     = entry.motif if not entry.present else None
            existing.updated_at = now
            updated += 1
        else:
            db.add(SupervisorPresenceCheck(
                id=uuid.uuid4(),
                superviseur_id=current_user.id,
                teacher_id=teacher_uuid,
                date_jour=target_date,
                present=entry.present,
                motif=entry.motif if not entry.present else None,
                created_at=now,
                updated_at=now,
            ))
            created += 1

    await db.flush()
    return {"created": created, "updated": updated, "total": created + updated}
