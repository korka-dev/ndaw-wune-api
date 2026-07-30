"""Endpoint App mobile — Progression (semaine/jour) des enseignants assignés
au superviseur, dérivée du dernier rapport journalier soumis par chacun.

Route :
  GET /app/supervisor/progression
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from app.core.deps import DB, SuperviseurUser
from app.models.user import User
from app.services.progression_service import get_latest_progression_by_teacher

router = APIRouter(prefix="/supervisor", tags=["App — Superviseur"])


class ProgressionItem(BaseModel):
    teacher_id:   str
    teacher_name: str
    ecole:        str | None = None
    semaine:      int | None = None
    jour_cours:   int | None = None
    date_rapport: str | None = None


class ProgressionPayload(BaseModel):
    items: list[ProgressionItem]


@router.get("/progression", response_model=ProgressionPayload)
async def supervisor_progression(current_user: SuperviseurUser, db: DB) -> ProgressionPayload:
    if not current_user.classes:
        return ProgressionPayload(items=[])

    teacher_ids: list[uuid.UUID] = []
    for id_str in current_user.classes:
        try:
            teacher_ids.append(uuid.UUID(id_str))
        except ValueError:
            continue
    if not teacher_ids:
        return ProgressionPayload(items=[])

    teachers = (
        await db.execute(select(User).where(User.id.in_(teacher_ids)).order_by(User.name))
    ).scalars().all()

    progress_map = await get_latest_progression_by_teacher(db, teacher_ids)

    items = [
        ProgressionItem(
            teacher_id=str(t.id),
            teacher_name=t.name,
            ecole=t.school.name if t.school else None,
            semaine=progress_map.get(t.id, {}).get("semaine"),
            jour_cours=progress_map.get(t.id, {}).get("jour_cours"),
            date_rapport=progress_map.get(t.id, {}).get("date_rapport"),
        )
        for t in teachers
    ]
    return ProgressionPayload(items=items)
