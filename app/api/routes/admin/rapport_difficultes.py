"""Routes admin — Gestion de la liste des difficultés du rapport journalier.

Cette liste est définie par l'admin et affichée dans l'étape « Difficultés »
du formulaire de rapport journalier de l'app mobile (synchronisée via /app/sync).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select

from app.core.deps import AdminUser, DB
from app.core.redis import invalidate_sync_caches
from app.models.rapport_difficulte import RapportDifficulte
from app.schemas.rapport_difficulte import (
    RapportDifficulteCreate,
    RapportDifficulteResponse,
    RapportDifficulteUpdate,
)

router = APIRouter(prefix="/rapport-difficultes", tags=["Admin — Difficultés de rapport"])


async def _get_or_404(db: DB, difficulte_id: uuid.UUID) -> RapportDifficulte:
    obj = (
        await db.execute(select(RapportDifficulte).where(RapportDifficulte.id == difficulte_id))
    ).scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=404, detail="Difficulté introuvable.")
    return obj


@router.get("", response_model=list[RapportDifficulteResponse])
async def list_rapport_difficultes(db: DB, _: AdminUser) -> list[RapportDifficulteResponse]:
    items = (
        await db.execute(select(RapportDifficulte).order_by(RapportDifficulte.ordre, RapportDifficulte.created_at))
    ).scalars().all()
    return items


@router.post("", response_model=RapportDifficulteResponse, status_code=status.HTTP_201_CREATED)
async def create_rapport_difficulte(body: RapportDifficulteCreate, db: DB, _: AdminUser) -> RapportDifficulteResponse:
    obj = RapportDifficulte(**body.model_dump())
    db.add(obj)
    await db.flush()
    await invalidate_sync_caches()
    return obj


@router.patch("/{difficulte_id}", response_model=RapportDifficulteResponse)
async def update_rapport_difficulte(
    difficulte_id: uuid.UUID, body: RapportDifficulteUpdate, db: DB, _: AdminUser
) -> RapportDifficulteResponse:
    obj = await _get_or_404(db, difficulte_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(obj, field, value)
    await db.flush()
    await invalidate_sync_caches()
    return obj


@router.delete("/{difficulte_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rapport_difficulte(difficulte_id: uuid.UUID, db: DB, _: AdminUser) -> Response:
    obj = await _get_or_404(db, difficulte_id)
    await db.delete(obj)
    await invalidate_sync_caches()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
