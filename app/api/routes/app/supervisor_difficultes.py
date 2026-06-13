"""Endpoint App mobile — Difficultés rencontrées par les enseignants assignés.

Route :
  GET /app/supervisor/difficultes → rapports journaliers des enseignants
      assignés au superviseur, contenant des difficultés signalées.
"""
from __future__ import annotations

import json
import uuid
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from app.core.deps import DB, SuperviseurUser
from app.models.rapport_journalier import RapportJournalier
from app.models.user import User

router = APIRouter(prefix="/supervisor", tags=["App — Superviseur"])


# ── Schémas ───────────────────────────────────────────────────────────────────

class DifficulteItem(BaseModel):
    id:                       str
    teacher_id:               str
    teacher_name:             str
    ecole:                    str
    date_rapport:             str
    difficultes:              list[str]
    autres_difficultes:       Optional[str] = None
    description_difficultes:  Optional[str] = None


class DifficultesPayload(BaseModel):
    items: list[DifficulteItem]
    total: int


# ── Route ─────────────────────────────────────────────────────────────────────

@router.get("/difficultes", response_model=DifficultesPayload)
async def supervisor_difficultes(current_user: SuperviseurUser, db: DB) -> DifficultesPayload:
    """
    Retourne les rapports journaliers des enseignants assignés au superviseur
    qui contiennent au moins une difficulté signalée (hors "Aucune").
    """
    if not current_user.classes:
        return DifficultesPayload(items=[], total=0)

    teacher_ids: list[uuid.UUID] = []
    for id_str in current_user.classes:
        try:
            teacher_ids.append(uuid.UUID(id_str))
        except ValueError:
            continue

    if not teacher_ids:
        return DifficultesPayload(items=[], total=0)

    teachers_result = await db.execute(select(User).where(User.id.in_(teacher_ids)))
    teachers_by_id = {t.id: t for t in teachers_result.scalars().all()}

    rapports = (
        await db.execute(
            select(RapportJournalier)
            .where(RapportJournalier.teacher_id.in_(teacher_ids))
            .order_by(RapportJournalier.date_rapport.desc(), RapportJournalier.created_at.desc())
        )
    ).scalars().all()

    items: list[DifficulteItem] = []
    for r in rapports:
        try:
            diffs = json.loads(r.difficultes) if r.difficultes else []
        except (json.JSONDecodeError, TypeError):
            diffs = []
        if not isinstance(diffs, list):
            diffs = []

        diffs = [d for d in diffs if d and d != "Aucune"]
        if not diffs:
            continue

        teacher = teachers_by_id.get(r.teacher_id)
        items.append(
            DifficulteItem(
                id=str(r.id),
                teacher_id=str(r.teacher_id),
                teacher_name=teacher.name if teacher else "Enseignant inconnu",
                ecole=r.ecole,
                date_rapport=r.date_rapport.isoformat(),
                difficultes=diffs,
                autres_difficultes=r.autres_difficultes,
                description_difficultes=r.description_difficultes,
            )
        )

    return DifficultesPayload(items=items, total=len(items))
