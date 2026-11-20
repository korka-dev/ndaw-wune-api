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
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.deps import DB, SuperviseurUser
from app.models.supervisor_presence import SupervisorPresenceCheck
from app.services.progression_service import active_session_for
from app.services.supervisor_service import get_supervised_teacher_ids

router = APIRouter(prefix="/supervisor", tags=["App — Superviseur"])


# ── Schémas ───────────────────────────────────────────────────────────────────

class PresenceEntryIn(BaseModel):
    teacher_id: str
    present:    bool
    motif:      Optional[str] = Field(default=None, max_length=1_000)


class PresenceCheckIn(BaseModel):
    date_jour: str       # ISO YYYY-MM-DD
    # Période du programme choisie par le superviseur avant le pointage
    semaine:    Optional[int] = Field(default=None, ge=1, le=53)
    jour_cours: Optional[int] = Field(default=None, ge=1, le=7)
    entries:   list[PresenceEntryIn] = Field(max_length=500)


class PresenceEntryOut(BaseModel):
    teacher_id: str
    present:    bool
    motif:      Optional[str] = None


class PresenceCheckOut(BaseModel):
    date_jour: str
    semaine:    Optional[int] = None
    jour_cours: Optional[int] = None
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

    # APP-06 : uniquement les enseignants actuellement supervisés.
    if rows:
        supervises = set(await get_supervised_teacher_ids(db, current_user))
        rows = [r for r in rows if r.teacher_id in supervises]

    first = rows[0] if rows else None
    return PresenceCheckOut(
        date_jour=target_date.isoformat(),
        semaine=first.semaine if first else None,
        jour_cours=first.jour_cours if first else None,
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
    if target_date > date.today() + timedelta(days=1):
        raise HTTPException(status_code=422, detail="date_jour ne peut pas être dans le futur.")

    # Validation complète avant toute écriture : chaque enseignant pointé doit
    # relever de ce superviseur (sinon refus, sans aucune mutation).
    parsed_entries = []
    vus: dict[uuid.UUID, int] = {}
    for i, entry in enumerate(body.entries):
        try:
            tid = uuid.UUID(entry.teacher_id)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"teacher_id invalide : {entry.teacher_id}")
        if tid in vus:
            # APP-29 : un doublon dans le lot est une erreur de saisie (422),
            # pas une violation d'unicité en 500.
            raise HTTPException(
                status_code=422,
                detail=f"Enseignant pointé deux fois dans le même lot (entrées {vus[tid]} et {i}).",
            )
        vus[tid] = i
        parsed_entries.append((tid, entry))
    supervised = set(await get_supervised_teacher_ids(db, current_user))
    if any(tid not in supervised for tid, _ in parsed_entries):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Un ou plusieurs enseignants ne relèvent pas de votre périmètre de supervision.",
        )

    now = datetime.now(timezone.utc)
    created = updated = 0
    # Campagne de rattachement (APP-22) : session active à la réception.
    active = await active_session_for(db, current_user)
    session_id = active.id if active else None

    for teacher_uuid, entry in parsed_entries:

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
            existing.semaine   = body.semaine
            existing.jour_cours = body.jour_cours
            existing.session_id = existing.session_id or session_id
            existing.updated_at = now
            updated += 1
        else:
            record = SupervisorPresenceCheck(
                id=uuid.uuid4(),
                superviseur_id=current_user.id,
                teacher_id=teacher_uuid,
                date_jour=target_date,
                session_id=session_id,
                semaine=body.semaine,
                jour_cours=body.jour_cours,
                present=entry.present,
                motif=entry.motif if not entry.present else None,
                created_at=now,
                updated_at=now,
            )
            try:
                async with db.begin_nested():  # envoi concurrent : l'unicité tranche
                    db.add(record)
                    await db.flush()
                created += 1
            except IntegrityError:
                existing = (await db.execute(
                    select(SupervisorPresenceCheck).where(
                        SupervisorPresenceCheck.superviseur_id == current_user.id,
                        SupervisorPresenceCheck.teacher_id     == teacher_uuid,
                        SupervisorPresenceCheck.date_jour      == target_date,
                    )
                )).scalar_one_or_none()
                if existing is None:
                    raise
                existing.present    = entry.present
                existing.motif      = entry.motif if not entry.present else None
                existing.semaine    = body.semaine
                existing.jour_cours = body.jour_cours
                existing.updated_at = now
                updated += 1

    await db.flush()
    return {"created": created, "updated": updated, "total": created + updated}
