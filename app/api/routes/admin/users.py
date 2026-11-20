from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Response, status

from app.core.deps import AdminUser, DB
from app.core.pagination import Page, Pagination
from app.models.user import UserRole
from app.schemas.user import SetPasswordRequest, UserCreate, UserResponse, UserUpdate
from app.services import user_service

router = APIRouter(prefix="/users", tags=["Admin — Comptes"])


@router.get("", response_model=Page[UserResponse])
async def list_users(db: DB, _: AdminUser, page: Pagination) -> Page[UserResponse]:
    """Liste les comptes admin et coordonnateur."""
    total, items = await user_service.list_admin_accounts(db, page.skip, page.limit)
    return Page(total=total, skip=page.skip, limit=page.limit, items=items)


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(body: UserCreate, db: DB, current_user: AdminUser) -> UserResponse:
    """Seul un administrateur peut créer des comptes admin/coordonnateur."""
    if body.role not in (UserRole.admin, UserRole.coordonnateur):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ce endpoint ne crée que des comptes admin ou coordonnateur.",
        )
    if current_user.role != UserRole.admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Seul un administrateur peut créer des comptes.",
        )
    return await user_service.create_user(db, body)


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(user_id: uuid.UUID, body: UserUpdate, db: DB, current_user: AdminUser) -> UserResponse:
    if current_user.role != UserRole.admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Seul un administrateur peut modifier ces comptes.",
        )
    return await user_service.update_user(db, user_id, body)


@router.post("/{user_id}/reset-password", response_model=UserResponse)
async def reset_password(
    user_id: uuid.UUID, body: SetPasswordRequest, db: DB, current_user: AdminUser
) -> UserResponse:
    """Réinitialise le mot de passe d'un compte admin/coordonnateur (APP-01/02).

    Réservé à un administrateur, jamais sur son propre compte (il passe par
    /auth/change-password). Toutes les sessions du compte sont invalidées.
    """
    if current_user.role != UserRole.admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Seul un administrateur peut réinitialiser ces comptes.",
        )
    target = await user_service.get_by_id(db, user_id)
    if target.role not in (UserRole.admin, UserRole.coordonnateur):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Utilisateur introuvable.")
    if target.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Pour votre propre compte, utilisez « Changer mon mot de passe ».",
        )
    return await user_service.admin_reset_password(db, user_id, body.new_password, body.force_change)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: uuid.UUID, db: DB, current_user: AdminUser) -> Response:
    if current_user.role != UserRole.admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Seul un administrateur peut supprimer des comptes.",
        )
    await user_service.delete_user(db, user_id, current_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
