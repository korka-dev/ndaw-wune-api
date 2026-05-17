from __future__ import annotations

import uuid

from fastapi import APIRouter, Response, status

from app.core.deps import AdminUser, DB
from app.core.pagination import Page, Pagination
from app.models.user import UserRole
from app.schemas.user import UserCreate, UserResponse, UserUpdate
from app.services import user_service

router = APIRouter(prefix="/teachers", tags=["Admin — Enseignants"])


@router.get("", response_model=Page[UserResponse])
async def list_teachers(db: DB, _: AdminUser, page: Pagination) -> Page[UserResponse]:
    total, items = await user_service.list_by_role(db, UserRole.enseignant, page.skip, page.limit)
    return Page(total=total, skip=page.skip, limit=page.limit, items=items)


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_teacher(body: UserCreate, db: DB, _: AdminUser) -> UserResponse:
    # force_role garantit que le rôle est toujours "enseignant", peu importe la valeur envoyée
    return await user_service.create_user(db, body, force_role=UserRole.enseignant)


@router.get("/{teacher_id}", response_model=UserResponse)
async def get_teacher(teacher_id: uuid.UUID, db: DB, _: AdminUser) -> UserResponse:
    return await user_service.get_by_id(db, teacher_id)


@router.patch("/{teacher_id}", response_model=UserResponse)
async def update_teacher(teacher_id: uuid.UUID, body: UserUpdate, db: DB, _: AdminUser) -> UserResponse:
    return await user_service.update_user(db, teacher_id, body)


@router.delete("/{teacher_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_teacher(teacher_id: uuid.UUID, db: DB, current_user: AdminUser) -> Response:
    await user_service.delete_user(db, teacher_id, current_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{teacher_id}/toggle-status", response_model=UserResponse)
async def toggle_status(teacher_id: uuid.UUID, db: DB, _: AdminUser) -> UserResponse:
    return await user_service.toggle_status(db, teacher_id)
