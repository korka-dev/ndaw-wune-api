"""Routes Admin — Remarques des utilisateurs de l'application mobile.

Routes :
  GET   /admin/remarques           → liste paginée (filtres catégorie/statut)
  PATCH /admin/remarques/{id}      → changer le statut et/ou répondre à l'auteur
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select

from app.core.deps import AdminUser, DB
from app.core.pagination import Page, Pagination
from app.models.remarque import Remarque

router = APIRouter(prefix="/remarques", tags=["Admin — Remarques"])


class RemarqueAdminOut(BaseModel):
    id:                str
    user_id:           Optional[str] = None
    user_name:         str
    user_role:         str
    ecole:             Optional[str] = None
    categorie:         str
    message:           str
    statut:            str
    reponse_admin:     Optional[str] = None
    reponse_admin_at:  Optional[str] = None
    created_at:        str


class RemarqueStatusIn(BaseModel):
    statut:        Optional[str] = None  # nouveau | traite
    reponse_admin: Optional[str] = None  # réponse envoyée à l'auteur de la remarque


def _to_out(r: Remarque) -> RemarqueAdminOut:
    return RemarqueAdminOut(
        id=str(r.id),
        user_id=str(r.user_id) if r.user_id else None,
        user_name=r.user_name,
        user_role=r.user_role,
        ecole=r.ecole,
        categorie=r.categorie,
        message=r.message,
        statut=r.statut,
        reponse_admin=r.reponse_admin,
        reponse_admin_at=r.reponse_admin_at.isoformat() if r.reponse_admin_at else None,
        created_at=r.created_at.isoformat(),
    )


@router.get("", response_model=Page[RemarqueAdminOut])
async def list_remarques(
    db: DB,
    _: AdminUser,
    page: Pagination,
    categorie: Optional[str] = None,
    statut: Optional[str] = None,
) -> Page[RemarqueAdminOut]:
    base = select(Remarque).order_by(Remarque.created_at.desc())
    if categorie:
        base = base.where(Remarque.categorie == categorie)
    if statut:
        base = base.where(Remarque.statut == statut)

    total = (await db.execute(
        select(func.count()).select_from(base.subquery())
    )).scalar_one()

    rows = (await db.execute(base.offset(page.skip).limit(page.limit))).scalars().all()
    return Page(total=total, skip=page.skip, limit=page.limit, items=[_to_out(r) for r in rows])


@router.patch("/{remarque_id}", response_model=RemarqueAdminOut)
async def update_remarque(
    remarque_id: uuid.UUID,
    body: RemarqueStatusIn,
    db: DB,
    _: AdminUser,
) -> RemarqueAdminOut:
    if body.statut is not None and body.statut not in ("nouveau", "traite"):
        raise HTTPException(status_code=422, detail="Statut invalide : 'nouveau' ou 'traite'.")

    remarque = (await db.execute(
        select(Remarque).where(Remarque.id == remarque_id)
    )).scalar_one_or_none()
    if remarque is None:
        raise HTTPException(status_code=404, detail="Remarque introuvable.")

    if body.reponse_admin is not None:
        texte = body.reponse_admin.strip()
        remarque.reponse_admin = texte or None
        remarque.reponse_admin_at = datetime.now(timezone.utc) if texte else None
        # Une réponse envoyée marque la remarque comme traitée, sauf statut explicite fourni
        if body.statut is None and texte:
            remarque.statut = "traite"

    if body.statut is not None:
        remarque.statut = body.statut

    await db.commit()
    await db.refresh(remarque)
    return _to_out(remarque)
