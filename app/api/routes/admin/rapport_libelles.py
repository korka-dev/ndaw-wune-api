"""Routes admin — Gestion des libellés des champs fixes des rapports (tuteur
et superviseur). Contrairement aux questions complémentaires, l'ensemble des
clés est fixe (pré-rempli par migration) : l'admin ne peut qu'éditer le texte.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.core.deps import AdminUser, DB
from app.core.redis import invalidate_sync_caches
from app.models.rapport_libelle import RapportLibelle
from app.schemas.rapport_libelle import RapportLibelleResponse, RapportLibelleUpdate

router = APIRouter(prefix="/rapport-libelles", tags=["Admin — Libellés de rapport"])


@router.get("", response_model=list[RapportLibelleResponse])
async def list_rapport_libelles(db: DB, _: AdminUser) -> list[RapportLibelleResponse]:
    items = (
        await db.execute(select(RapportLibelle).order_by(RapportLibelle.cible, RapportLibelle.cle))
    ).scalars().all()
    return items


@router.patch("/{cle}", response_model=RapportLibelleResponse)
async def update_rapport_libelle(
    cle: str, body: RapportLibelleUpdate, db: DB, _: AdminUser
) -> RapportLibelleResponse:
    obj = (
        await db.execute(select(RapportLibelle).where(RapportLibelle.cle == cle))
    ).scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=404, detail="Libellé introuvable.")
    obj.texte = body.texte
    await db.flush()
    await invalidate_sync_caches()
    return obj
