"""Routes admin — Gestion des classes par école."""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import select, func

from app.core.deps import AdminUser, DB
from app.core.pagination import Page, Pagination
from app.models.school_classe import SchoolClasse
from app.models.school import School
from app.schemas.school_classe import SchoolClasseCreate, SchoolClasseResponse, SchoolClasseUpdate

router = APIRouter(prefix="/classes", tags=["Admin — Classes"])


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_or_404(db, classe_id: uuid.UUID) -> SchoolClasse:
    result = await db.execute(
        select(SchoolClasse).where(SchoolClasse.id == classe_id)
    )
    obj = result.scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=404, detail="Classe introuvable.")
    return obj


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("", response_model=Page[SchoolClasseResponse])
async def list_classes(
    db: DB,
    _: AdminUser,
    page: Pagination,
    school_id: Optional[uuid.UUID] = Query(None),
    niveau:    Optional[str]        = Query(None),
) -> Page[SchoolClasseResponse]:
    q = select(SchoolClasse).order_by(SchoolClasse.niveau, SchoolClasse.name)
    if school_id:
        q = q.where(SchoolClasse.school_id == school_id)
    if niveau:
        q = q.where(SchoolClasse.niveau == niveau)

    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    items = (await db.execute(q.offset(page.skip).limit(page.limit))).scalars().all()
    return Page(total=total, skip=page.skip, limit=page.limit, items=items)


@router.post("", response_model=SchoolClasseResponse, status_code=status.HTTP_201_CREATED)
async def create_classe(body: SchoolClasseCreate, db: DB, _: AdminUser) -> SchoolClasseResponse:
    # Vérifier que l'école existe
    school = (await db.execute(select(School).where(School.id == body.school_id))).scalar_one_or_none()
    if not school:
        raise HTTPException(status_code=404, detail="École introuvable.")

    # Vérifier unicité (école + nom)
    existing = (await db.execute(
        select(SchoolClasse).where(
            SchoolClasse.school_id == body.school_id,
            SchoolClasse.name == body.name,
        )
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"La classe « {body.name} » existe déjà dans cette école."
        )

    obj = SchoolClasse(**body.model_dump())
    db.add(obj)
    await db.flush()
    await db.refresh(obj, attribute_names=["school"])
    return obj


@router.get("/{classe_id}", response_model=SchoolClasseResponse)
async def get_classe(classe_id: uuid.UUID, db: DB, _: AdminUser) -> SchoolClasseResponse:
    return await _get_or_404(db, classe_id)


@router.patch("/{classe_id}", response_model=SchoolClasseResponse)
async def update_classe(
    classe_id: uuid.UUID, body: SchoolClasseUpdate, db: DB, _: AdminUser
) -> SchoolClasseResponse:
    obj = await _get_or_404(db, classe_id)

    data = body.model_dump(exclude_none=True)

    # Si on change le nom, vérifier l'unicité
    new_name = data.get("name", obj.name)
    if new_name != obj.name:
        conflict = (await db.execute(
            select(SchoolClasse).where(
                SchoolClasse.school_id == obj.school_id,
                SchoolClasse.name == new_name,
                SchoolClasse.id != obj.id,
            )
        )).scalar_one_or_none()
        if conflict:
            raise HTTPException(
                status_code=409,
                detail=f"La classe « {new_name} » existe déjà dans cette école."
            )

    for field, value in data.items():
        setattr(obj, field, value)

    await db.flush()
    await db.refresh(obj, attribute_names=["school"])
    return obj


@router.delete("/{classe_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_classe(classe_id: uuid.UUID, db: DB, _: AdminUser) -> Response:
    obj = await _get_or_404(db, classe_id)
    await db.delete(obj)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
