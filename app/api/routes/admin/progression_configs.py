"""Routes admin — Configuration du nombre de semaines/jours du programme,
par école et/ou par session (repli global si non précisé)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select

from app.core.deps import AdminUser, DB
from app.models.progression_config import ProgressionConfig
from app.schemas.progression_config import (
    ProgressionConfigCreate,
    ProgressionConfigResponse,
    ProgressionConfigUpdate,
)

router = APIRouter(prefix="/progression-configs", tags=["Admin — Progression"])


async def _get_or_404(db: DB, config_id: uuid.UUID) -> ProgressionConfig:
    obj = (
        await db.execute(select(ProgressionConfig).where(ProgressionConfig.id == config_id))
    ).scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=404, detail="Configuration introuvable.")
    return obj


@router.get("", response_model=list[ProgressionConfigResponse])
async def list_progression_configs(db: DB, _: AdminUser) -> list[ProgressionConfigResponse]:
    items = (
        await db.execute(select(ProgressionConfig).order_by(ProgressionConfig.created_at))
    ).scalars().all()
    return items


@router.post("", response_model=ProgressionConfigResponse, status_code=status.HTTP_201_CREATED)
async def create_progression_config(
    body: ProgressionConfigCreate, db: DB, _: AdminUser
) -> ProgressionConfigResponse:
    existing = (
        await db.execute(
            select(ProgressionConfig).where(
                ProgressionConfig.school_id == body.school_id,
                ProgressionConfig.session_id == body.session_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail="Une configuration existe déjà pour cette combinaison école/session.",
        )
    obj = ProgressionConfig(**body.model_dump())
    db.add(obj)
    await db.flush()
    return obj


@router.patch("/{config_id}", response_model=ProgressionConfigResponse)
async def update_progression_config(
    config_id: uuid.UUID, body: ProgressionConfigUpdate, db: DB, _: AdminUser
) -> ProgressionConfigResponse:
    obj = await _get_or_404(db, config_id)
    data = body.model_dump(exclude_unset=True)

    # La portée (école / session) peut changer : vérifier qu'aucune autre
    # configuration ne couvre déjà la nouvelle combinaison (contrainte unique).
    new_school  = data.get("school_id",  obj.school_id)
    new_session = data.get("session_id", obj.session_id)
    if (new_school, new_session) != (obj.school_id, obj.session_id):
        clash = (
            await db.execute(
                select(ProgressionConfig).where(
                    ProgressionConfig.school_id == new_school,
                    ProgressionConfig.session_id == new_session,
                    ProgressionConfig.id != obj.id,
                )
            )
        ).scalar_one_or_none()
        if clash is not None:
            raise HTTPException(
                status_code=409,
                detail="Une configuration existe déjà pour cette combinaison école/session.",
            )

    for field, value in data.items():
        setattr(obj, field, value)
    await db.flush()
    return obj


@router.delete("/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_progression_config(config_id: uuid.UUID, db: DB, _: AdminUser) -> Response:
    obj = await _get_or_404(db, config_id)
    await db.delete(obj)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
