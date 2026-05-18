"""
Endpoints Admin — Superviseurs (rôle coordonnateur).
Les superviseurs suivent les enseignants sur le terrain.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Response, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.deps import AdminUser, DB
from app.core.pagination import Page, Pagination
from app.models.user import User, UserRole
from app.schemas.user import UserCreate, UserResponse, UserUpdate
from app.services import user_service

router = APIRouter(prefix="/superviseurs", tags=["Admin — Superviseurs"])


@router.get("", response_model=Page[UserResponse])
async def list_superviseurs(db: DB, _: AdminUser, page: Pagination) -> Page[UserResponse]:
    total, items = await user_service.list_by_role(db, UserRole.coordonnateur, page.skip, page.limit)
    return Page(total=total, skip=page.skip, limit=page.limit, items=items)


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_superviseur(body: UserCreate, db: DB, _: AdminUser) -> UserResponse:
    return await user_service.create_user(db, body, force_role=UserRole.coordonnateur)


@router.get("/{superviseur_id}", response_model=UserResponse)
async def get_superviseur(superviseur_id: uuid.UUID, db: DB, _: AdminUser) -> UserResponse:
    return await user_service.get_by_id(db, superviseur_id)


@router.patch("/{superviseur_id}", response_model=UserResponse)
async def update_superviseur(
    superviseur_id: uuid.UUID, body: UserUpdate, db: DB, _: AdminUser
) -> UserResponse:
    return await user_service.update_user(db, superviseur_id, body)


@router.delete("/{superviseur_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_superviseur(
    superviseur_id: uuid.UUID, db: DB, current_user: AdminUser
) -> Response:
    await user_service.delete_user(db, superviseur_id, current_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{superviseur_id}/toggle-status", response_model=UserResponse)
async def toggle_status(superviseur_id: uuid.UUID, db: DB, _: AdminUser) -> UserResponse:
    return await user_service.toggle_status(db, superviseur_id)


@router.post("/{superviseur_id}/assign-teachers", status_code=status.HTTP_204_NO_CONTENT)
async def assign_teachers(
    superviseur_id: uuid.UUID,
    body: dict,
    db: DB,
    _: AdminUser,
) -> Response:
    """
    Assigne une liste d'enseignants à un superviseur via le champ `classes`
    (stockage des IDs enseignants assignés — adapté au modèle existant).
    Retourne 204 No Content.
    """
    # On vérifie que le superviseur existe
    await user_service.get_by_id(db, superviseur_id)
    assigned_ids: list[str] = [str(i) for i in body.get("assigned_teacher_ids", [])]

    # Stocker les IDs assignés dans le champ `classes` du superviseur
    # (champ ARRAY(String) polyvalent en attendant une table dédiée)
    result = await db.execute(select(User).where(User.id == superviseur_id))
    sup = result.scalar_one_or_none()
    if sup:
        sup.classes = assigned_ids
        await db.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
