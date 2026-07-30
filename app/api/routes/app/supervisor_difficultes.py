"""Endpoint App mobile — Difficultés rencontrées par les enseignants assignés.

Routes :
  GET   /app/supervisor/difficultes → rapports journaliers des enseignants
        assignés au superviseur, contenant des difficultés signalées
        (avec absences et état de résolution par difficulté).
  PATCH /app/supervisor/difficultes/{rapport_id}/resolve → marquer une
        difficulté individuelle comme résolue/non résolue.
"""
from __future__ import annotations

import json
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.core.deps import DB, SuperviseurUser
from app.models.rapport_journalier import RapportJournalier
from app.models.user import User
from app.schemas.rapport_difficulte_resolution import (
    DifficulteResolutionUpdate,
    RapportDifficulteResolutionResponse,
)
from app.services.difficulte_resolution_service import get_resolutions_map, set_resolution

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
    nb_absences:              int = 0
    absents:                  Optional[str] = None
    resolutions:              dict[str, bool] = {}


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

    kept_rapports: list[tuple[RapportJournalier, list[str]]] = []
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
        kept_rapports.append((r, diffs))

    resolutions_map = await get_resolutions_map(db, [r.id for r, _ in kept_rapports])

    items: list[DifficulteItem] = []
    for r, diffs in kept_rapports:
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
                nb_absences=r.nb_absences,
                absents=r.absents,
                resolutions=resolutions_map.get(r.id, {}),
            )
        )

    return DifficultesPayload(items=items, total=len(items))


@router.patch("/difficultes/{rapport_id}/resolve", response_model=RapportDifficulteResolutionResponse)
async def resolve_supervisor_difficulte(
    rapport_id: uuid.UUID,
    body: DifficulteResolutionUpdate,
    current_user: SuperviseurUser,
    db: DB,
) -> RapportDifficulteResolutionResponse:
    """Marque une difficulté individuelle d'un rapport comme résolue/non résolue.

    Réservé au superviseur assigné à l'enseignant auteur du rapport.
    """
    rapport = (
        await db.execute(select(RapportJournalier).where(RapportJournalier.id == rapport_id))
    ).scalar_one_or_none()
    if rapport is None:
        raise HTTPException(status_code=404, detail="Rapport introuvable.")

    allowed_teacher_ids = set()
    for id_str in current_user.classes or []:
        try:
            allowed_teacher_ids.add(uuid.UUID(id_str))
        except ValueError:
            continue
    if rapport.teacher_id not in allowed_teacher_ids:
        raise HTTPException(status_code=403, detail="Ce rapport ne concerne pas un enseignant assigné.")

    obj = await set_resolution(
        db,
        rapport_id=rapport_id,
        difficulte_label=body.difficulte_label,
        resolue=body.resolue,
        commentaire_resolution=body.commentaire_resolution,
        current_user=current_user,
    )
    await db.commit()
    return obj
