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
    items_orm = (await db.execute(stmt.offset(page.skip).limit(page.limit))).scalars().all()

    # ── Résolution école / tuteur en 2 requêtes groupées (pas de relation ORM) ──
    from app.models.school import School
    from app.models.user import User

    school_ids = {i.school_id for i in items_orm if i.school_id}
    teacher_ids = {i.teacher_id for i in items_orm if i.teacher_id}

    schools_by_id: dict[uuid.UUID, School] = {}
    if school_ids:
        rows = (await db.execute(select(School).where(School.id.in_(school_ids)))).scalars().all()
        schools_by_id = {s.id: s for s in rows}

    teachers_by_id: dict[uuid.UUID, User] = {}
    if teacher_ids:
        rows = (await db.execute(select(User).where(User.id.in_(teacher_ids)))).scalars().all()
        teachers_by_id = {u.id: u for u in rows}

    items = [
        EleveRemplacementResponse(
            id=i.id,
            ancien_eleve_id=i.ancien_eleve_id,
            ancien_eleve_nom=i.ancien_eleve_nom,
            nouveau_eleve_id=i.nouveau_eleve_id,
            nouveau_eleve_nom=i.nouveau_eleve_nom,
            motif=i.motif,
            teacher_id=i.teacher_id,
            teacher_name=teachers_by_id[i.teacher_id].name if i.teacher_id in teachers_by_id else None,
            school_id=i.school_id,
            school_name=schools_by_id[i.school_id].name if i.school_id in schools_by_id else None,
            school_commune=schools_by_id[i.school_id].city if i.school_id in schools_by_id else None,
            classe=i.classe,
            date_remplacement=i.date_remplacement,
        )
        for i in items_orm
    ]
    return Page(total=total, skip=page.skip, limit=page.limit, items=items)
