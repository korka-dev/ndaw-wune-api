"""Endpoints Admin — Inspecteurs (lot L8).

Un inspecteur se connecte à l'application mobile (par son téléphone) et
consulte, en lecture seule, les écoles de son IEF et les rapports de leurs
tuteurs et superviseurs. L'admin crée le compte et choisit l'IEF parmi celles
portées par les écoles (`schools.region`).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core.deps import AdminUser, DB
from app.core.pagination import Page, Pagination
from app.models.user import User, UserRole
from app.schemas.user import UserCreate, UserResponse, UserUpdate
from app.services import user_service
from app.services.archivage import non_archive
from app.services.inspecteur_service import ief_canonique, liste_iefs

router = APIRouter(prefix="/inspecteurs", tags=["Admin — Inspecteurs"])

_ROLES = (UserRole.inspecteur,)


@router.get("", response_model=Page[UserResponse])
async def list_inspecteurs(db: DB, _: AdminUser, page: Pagination,
                           archives: bool = Query(False, description="Inclure les éléments archivés")) -> Page[UserResponse]:
    base = (
        select(User).where(User.role == UserRole.inspecteur, non_archive(User, archives))
        .options(selectinload(User.school)).order_by(User.name)
    )
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    items = (await db.execute(base.offset(page.skip).limit(page.limit))).scalars().all()
    return Page(total=total, skip=page.skip, limit=page.limit, items=items)


@router.get("/iefs")
async def iefs(db: DB, _: AdminUser) -> list[dict]:
    """IEF disponibles (libellés des écoles) et nombre d'écoles actives de chacune."""
    return await liste_iefs(db)


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_inspecteur(body: UserCreate, db: DB, _: AdminUser) -> UserResponse:
    """Crée un inspecteur ; mot de passe temporaire à changer à la première connexion."""
    if not body.phone:
        raise HTTPException(status_code=422, detail="Le téléphone est obligatoire : l'inspecteur se connecte à l'application avec.")
    ief = await ief_canonique(db, body.ief)
    body = body.model_copy(update={
        "ief": ief, "school_id": None, "niveau": None, "classes": None,
        "groupe_recherche": None, "app_access": "full",
    })
    return await user_service.create_user(db, body, force_role=UserRole.inspecteur)


@router.get("/{inspecteur_id}", response_model=UserResponse)
async def get_inspecteur(inspecteur_id: uuid.UUID, db: DB, current_user: AdminUser) -> UserResponse:
    return await user_service.get_managed(db, inspecteur_id, current_user, _ROLES)


@router.patch("/{inspecteur_id}", response_model=UserResponse)
async def update_inspecteur(inspecteur_id: uuid.UUID, body: UserUpdate, db: DB, current_user: AdminUser) -> UserResponse:
    champs = body.model_dump(exclude_unset=True)
    interdits = {"school_id", "niveau", "classes", "groupe_recherche", "app_access", "motif_protocole"} & champs.keys()
    if interdits:
        raise HTTPException(status_code=422, detail=f"Champs sans objet pour un inspecteur : {', '.join(sorted(interdits))}.")
    if "ief" in champs:
        body = body.model_copy(update={"ief": await ief_canonique(db, body.ief)})
    return await user_service.update_user(db, inspecteur_id, body, current_user, _ROLES)


@router.delete("/{inspecteur_id}")
async def delete_inspecteur(inspecteur_id: uuid.UUID, db: DB, current_user: AdminUser,
                            definitif: bool = Query(False, description="Suppression physique, seulement sans historique (APP-15)")) -> dict:
    return await user_service.delete_user(db, inspecteur_id, current_user.id, current_user, _ROLES, definitif=definitif)


@router.post("/{inspecteur_id}/restaurer")
async def restore_inspecteur(inspecteur_id: uuid.UUID, db: DB, current_user: AdminUser) -> dict:
    return await user_service.restore_user(db, inspecteur_id, current_user, _ROLES)


@router.post("/{inspecteur_id}/toggle-status", response_model=UserResponse)
async def toggle_status(inspecteur_id: uuid.UUID, db: DB, current_user: AdminUser) -> UserResponse:
    return await user_service.toggle_status(db, inspecteur_id, current_user, _ROLES)
