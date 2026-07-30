"""Routes admin — Historique des remplacements d'élève effectués par les
tuteurs (lecture seule, la création se fait depuis l'app mobile)."""
from __future__ import annotations

import uuid
from datetime import date
from typing import Optional

from sqlalchemy import select

from app.core.deps import AdminUser, DB
from app.core.pagination import Page, Pagination
from app.models.eleve_remplacement import EleveRemplacement
from app.schemas.eleve_remplacement import EleveRemplacementResponse
from fastapi import APIRouter

router = APIRouter(prefix="/remplacements", tags=["Admin — Remplacements élève"])


@router.get("", response_model=Page[EleveRemplacementResponse])
async def list_eleve_remplacements(
    db: DB,
    _: AdminUser,
    page: Pagination,
    school_id: Optional[uuid.UUID] = None,
    teacher_id: Optional[uuid.UUID] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> Page[EleveRemplacementResponse]:
    stmt = select(EleveRemplacement)
    if school_id is not None:
        stmt = stmt.where(EleveRemplacement.school_id == school_id)
    if teacher_id is not None:
        stmt = stmt.where(EleveRemplacement.teacher_id == teacher_id)
    if date_from is not None:
        stmt = stmt.where(EleveRemplacement.date_remplacement >= date_from)
    if date_to is not None:
        stmt = stmt.where(EleveRemplacement.date_remplacement <= date_to)
    stmt = stmt.order_by(EleveRemplacement.date_remplacement.desc(), EleveRemplacement.created_at.desc())

    from sqlalchemy import func
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    items = (await db.execute(stmt.offset(page.skip).limit(page.limit))).scalars().all()
    return Page(total=total, skip=page.skip, limit=page.limit, items=items)
