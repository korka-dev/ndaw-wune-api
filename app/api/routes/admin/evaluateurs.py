"""
Endpoints Admin — Évaluateurs.
Les évaluateurs évaluent la qualité des séances (rôle coordonnateur avec flag spécial).
Pour simplifier : on les gère comme des coordonnateurs avec title="evaluateur".
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Response, status
from sqlalchemy import select

from app.core.deps import AdminUser, DB
from app.core.pagination import Page, Pagination
from app.models.user import User, UserRole
from app.schemas.user import UserCreate, UserResponse, UserUpdate
from app.services import user_service

router = APIRouter(prefix="/evaluateurs", tags=["Admin — Évaluateurs"])


def _is_evaluateur(user: User) -> bool:
    return user.role == UserRole.coordonnateur and user.title == "evaluateur"


@router.get("", response_model=Page[UserResponse])
async def list_evaluateurs(db: DB, _: AdminUser, page: Pagination) -> Page[UserResponse]:
    """Liste tous les utilisateurs dont le title = 'evaluateur'."""
    from sqlalchemy import func
    from app.core.database import Base  # noqa

    base = (
        select(User)
        .where(User.role == UserRole.coordonnateur, User.title == "evaluateur")
        .order_by(User.name)
    )
    from sqlalchemy import func as sqlfunc
    total = (
        await db.execute(select(sqlfunc.count()).select_from(base.subquery()))
    ).scalar_one()
    items = (await db.execute(base.offset(page.skip).limit(page.limit))).scalars().all()
    return Page(total=total, skip=page.skip, limit=page.limit, items=items)


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_evaluateur(body: UserCreate, db: DB, _: AdminUser) -> UserResponse:
    """Crée un évaluateur (coordonnateur avec title='evaluateur')."""
    body_data = body.model_copy(update={"title": "evaluateur"})
    return await user_service.create_user(db, body_data, force_role=UserRole.coordonnateur)


@router.get("/{evaluateur_id}", response_model=UserResponse)
async def get_evaluateur(evaluateur_id: uuid.UUID, db: DB, _: AdminUser) -> UserResponse:
    return await user_service.get_by_id(db, evaluateur_id)


@router.patch("/{evaluateur_id}", response_model=UserResponse)
async def update_evaluateur(
    evaluateur_id: uuid.UUID, body: UserUpdate, db: DB, _: AdminUser
) -> UserResponse:
    return await user_service.update_user(db, evaluateur_id, body)


@router.delete("/{evaluateur_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_evaluateur(
    evaluateur_id: uuid.UUID, db: DB, current_user: AdminUser
) -> Response:
    await user_service.delete_user(db, evaluateur_id, current_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{evaluateur_id}/toggle-status", response_model=UserResponse)
async def toggle_status(evaluateur_id: uuid.UUID, db: DB, _: AdminUser) -> UserResponse:
    return await user_service.toggle_status(db, evaluateur_id)


@router.post("/{evaluateur_id}/assign-teachers", status_code=status.HTTP_204_NO_CONTENT)
async def assign_teachers(
    evaluateur_id: uuid.UUID, body: dict, db: DB, _: AdminUser
) -> Response:
    await user_service.get_by_id(db, evaluateur_id)
    assigned_ids: list[str] = [str(i) for i in body.get("assigned_teacher_ids", [])]
    result = await db.execute(select(User).where(User.id == evaluateur_id))
    ev = result.scalar_one_or_none()
    if ev:
        ev.classes = assigned_ids
        await db.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
