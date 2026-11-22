"""Routes admin — clés d'accès de service (lot L6, D9.4).

  GET    /admin/cles-service          liste (jamais la valeur)
  POST   /admin/cles-service          création : la valeur est renvoyée UNE fois
  POST   /admin/cles-service/{id}/revoquer
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app.core.cle_service import nouvelle_cle
from app.core.deps import DB, AdminUser
from app.models.cle_service import PORTEES_CLE, CleService
from app.models.user import UserRole
from app.services.audit_service import journaliser

router = APIRouter(prefix="/cles-service", tags=["Admin — Clés de service"])


class CleIn(BaseModel):
    nom: str = Field(min_length=3, max_length=200)
    portees: list[str] = Field(min_length=1)
    expire_le: Optional[datetime] = None

    @field_validator("portees")
    @classmethod
    def _portees(cls, v):
        inconnues = [p for p in v if p not in PORTEES_CLE]
        if inconnues:
            raise ValueError(f"portées inconnues : {', '.join(inconnues)} (attendu : {', '.join(PORTEES_CLE)})")
        return sorted(set(v))


def _out(c: CleService) -> dict:
    return {"id": c.id, "nom": c.nom, "prefixe": c.prefixe, "portees": c.portees, "expire_le": c.expire_le,
            "revoquee_le": c.revoquee_le, "derniere_utilisation": c.derniere_utilisation, "created_at": c.created_at,
            "active": c.revoquee_le is None and (c.expire_le is None or c.expire_le > datetime.now(timezone.utc))}


def _admin_strict(user) -> None:
    if user.role != UserRole.admin:
        raise HTTPException(403, "La gestion des clés de service est réservée aux administrateurs.")


@router.get("")
async def lister(db: DB, user: AdminUser) -> list[dict]:
    return [_out(c) for c in (await db.execute(select(CleService).order_by(CleService.created_at.desc()))).scalars()]


@router.post("", status_code=201)
async def creer(body: CleIn, db: DB, user: AdminUser) -> dict:
    _admin_strict(user)
    valeur, prefixe, digest = nouvelle_cle()
    c = CleService(nom=body.nom, prefixe=prefixe, empreinte=digest, portees=body.portees,
                   expire_le=body.expire_le, cree_par_id=user.id)
    db.add(c)
    await db.flush()
    journaliser(db, acteur=user, action="creation", entity="Clés de service",
                description=f"Clé « {body.nom} » ({', '.join(body.portees)})", cible_type="cle_service",
                cible_id=c.id, path="/cles-service")
    return {**_out(c), "valeur": valeur,
            "avertissement": "Copiez cette valeur maintenant : elle ne sera plus jamais affichée."}


@router.post("/{cle_id}/revoquer")
async def revoquer(cle_id: uuid.UUID, db: DB, user: AdminUser) -> dict:
    _admin_strict(user)
    c = (await db.execute(select(CleService).where(CleService.id == cle_id))).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, "Clé introuvable.")
    if c.revoquee_le is None:
        c.revoquee_le = datetime.now(timezone.utc)
        journaliser(db, acteur=user, action="revocation", entity="Clés de service", description=f"Clé « {c.nom} » révoquée",
                    cible_type="cle_service", cible_id=c.id, path=f"/cles-service/{c.id}/revoquer")
    return _out(c)
