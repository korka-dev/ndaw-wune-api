"""Routes admin — Résolution des difficultés signalées dans les rapports
journaliers (même mécanisme que côté app mobile superviseur)."""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.core.deps import AdminUser, DB
from app.models.rapport_difficulte_resolution import RapportDifficulteResolution
from app.models.rapport_journalier import RapportJournalier
from app.schemas.rapport_difficulte_resolution import (
    DifficulteResolutionUpdate,
    RapportDifficulteResolutionResponse,
)
from app.services.difficulte_resolution_service import set_resolution

router = APIRouter(prefix="/rapport-difficulte-resolutions", tags=["Admin — Résolution des difficultés"])


@router.get("", response_model=list[RapportDifficulteResolutionResponse])
async def list_rapport_difficulte_resolutions(
    db: DB,
    _: AdminUser,
    rapport_id: Optional[uuid.UUID] = Query(default=None),
    resolue: Optional[bool] = Query(default=None),
) -> list[RapportDifficulteResolutionResponse]:
    stmt = select(RapportDifficulteResolution)
    if rapport_id is not None:
        stmt = stmt.where(RapportDifficulteResolution.rapport_id == rapport_id)
    if resolue is not None:
        stmt = stmt.where(RapportDifficulteResolution.resolue == resolue)
    items = (await db.execute(stmt.order_by(RapportDifficulteResolution.created_at.desc()))).scalars().all()
    return items


@router.patch("/{rapport_id}/resolve", response_model=RapportDifficulteResolutionResponse)
async def resolve_rapport_difficulte(
    rapport_id: uuid.UUID,
    body: DifficulteResolutionUpdate,
    current_user: AdminUser,
    db: DB,
) -> RapportDifficulteResolutionResponse:
    rapport = (
        await db.execute(select(RapportJournalier).where(RapportJournalier.id == rapport_id))
    ).scalar_one_or_none()
    if rapport is None:
        raise HTTPException(status_code=404, detail="Rapport introuvable.")

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
