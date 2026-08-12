"""Endpoints Admin — Comptes tuteurs & superviseurs.

Liste unifiée des deux rôles de terrain (les comptes admin/coordonnateur sont
gérés séparément par `/admin/users`), avec réinitialisation de mot de passe.

⚠️  Les mots de passe existants ne peuvent PAS être affichés. Ils sont hachés
avec bcrypt (à sens unique, voir app/core/security.py) : la valeur en clair
n'est stockée nulle part, ni ici ni ailleurs, et ne peut être retrouvée sous
aucune forme — seulement remplacée par une nouvelle valeur choisie par l'admin.
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.core.deps import AdminUser, DB
from app.core.pagination import Page, Pagination
from app.models.school import School
from app.models.user import User, UserRole
from app.schemas.user import SetPasswordRequest, UserResponse
from app.services import user_service

router = APIRouter(prefix="/comptes-terrain", tags=["Admin — Comptes tuteurs & superviseurs"])

_ROLES_TERRAIN = (UserRole.enseignant, UserRole.superviseur)


@router.get("", response_model=Page[UserResponse])
async def list_comptes_terrain(
    db: DB,
    _: AdminUser,
    page: Pagination,
    role:   Optional[UserRole] = None,   # enseignant | superviseur — omis = les deux
    search: Optional[str]      = None,   # nom, téléphone ou école
) -> Page[UserResponse]:
    if role is not None and role not in _ROLES_TERRAIN:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Ce endpoint ne liste que les tuteurs et superviseurs.",
        )
    roles = [role] if role else list(_ROLES_TERRAIN)

    base = select(User).where(User.role.in_(roles))
    if search:
        like = f"%{search.strip()}%"
        base = base.where(or_(
            User.name.ilike(like),
            User.phone.ilike(like),
            User.school.has(School.name.ilike(like)),
        ))
    base = base.order_by(User.name)

    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    items = (await db.execute(
        base.options(selectinload(User.school)).offset(page.skip).limit(page.limit)
    )).scalars().all()
    return Page(total=total, skip=page.skip, limit=page.limit, items=items)


@router.post("/{user_id}/reset-password", response_model=UserResponse)
async def reset_password(user_id: uuid.UUID, body: SetPasswordRequest, db: DB, _: AdminUser) -> UserResponse:
    target = await user_service.get_by_id(db, user_id)
    if target.role not in _ROLES_TERRAIN:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Ce endpoint ne concerne que les comptes tuteurs et superviseurs "
            "— les comptes admin/coordonnateur se gèrent depuis « Comptes utilisateurs ».",
        )
    return await user_service.admin_reset_password(db, user_id, body.new_password, body.force_change)
