from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.core.deps import AdminUser, DB
from app.core.pagination import Page, Pagination
from app.models.seance import RapportProf
from app.schemas.seance import RapportResponse
from app.services import seance_service

router = APIRouter(prefix="/rapports", tags=["Admin — Rapports"])


@router.get("", response_model=Page[RapportResponse])
async def list_rapports(
    db: DB,
    _: AdminUser,
    page: Pagination,
    session_id: uuid.UUID | None = None,
    teacher_id: uuid.UUID | None = None,
) -> Page[RapportResponse]:
    total, items = await seance_service.list_admin_rapports(
        db, session_id, teacher_id, page.skip, page.limit
    )
    return Page(total=total, skip=page.skip, limit=page.limit, items=items)


@router.get("/{rapport_id}", response_model=RapportResponse)
async def get_rapport(rapport_id: uuid.UUID, db: DB, _: AdminUser) -> RapportResponse:
    result = await db.execute(select(RapportProf).where(RapportProf.id == rapport_id))
    rapport = result.scalar_one_or_none()
    if rapport is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rapport introuvable.")
    return rapport
