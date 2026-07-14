"""Endpoints App mobile — Remarques / signalement de problèmes hors application.

L'assistant de signalement de l'app (tuteurs et superviseurs) permet de
remonter des problèmes qui ne relèvent pas du rapport journalier
(ex: manque de matériel, problème de local…).

Routes :
  POST /app/remarques → soumettre une remarque
  GET  /app/remarques → mes remarques déjà envoyées
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from app.core.deps import DB, MobileUser
from app.models.remarque import Remarque

router = APIRouter(prefix="/remarques", tags=["App — Remarques"])

CATEGORIES = {"materiel", "local", "eleves", "securite", "autre"}


class RemarqueIn(BaseModel):
    categorie: str
    message:   str
    ecole:     Optional[str] = None


class RemarqueOut(BaseModel):
    id:         str
    categorie:  str
    message:    str
    ecole:      Optional[str] = None
    statut:     str
    created_at: str


@router.post("", response_model=RemarqueOut, status_code=status.HTTP_201_CREATED)
async def create_remarque(body: RemarqueIn, current_user: MobileUser, db: DB) -> RemarqueOut:
    categorie = (body.categorie or "").strip().lower()
    if categorie not in CATEGORIES:
        raise HTTPException(
            status_code=422,
            detail=f"Catégorie invalide. Valeurs possibles : {', '.join(sorted(CATEGORIES))}.",
        )
    message = (body.message or "").strip()
    if not message:
        raise HTTPException(status_code=422, detail="Le message ne peut pas être vide.")

    ecole = (body.ecole or "").strip() or None
    if not ecole and current_user.school:
        ecole = current_user.school.name

    remarque = Remarque(
        id=uuid.uuid4(),
        user_id=current_user.id,
        user_name=current_user.name,
        user_role=current_user.role.value,
        ecole=ecole,
        categorie=categorie,
        message=message,
    )
    db.add(remarque)
    await db.commit()
    await db.refresh(remarque)

    return RemarqueOut(
        id=str(remarque.id),
        categorie=remarque.categorie,
        message=remarque.message,
        ecole=remarque.ecole,
        statut=remarque.statut,
        created_at=remarque.created_at.isoformat(),
    )


@router.get("", response_model=list[RemarqueOut])
async def list_my_remarques(current_user: MobileUser, db: DB) -> list[RemarqueOut]:
    rows = (await db.execute(
        select(Remarque)
        .where(Remarque.user_id == current_user.id)
        .order_by(Remarque.created_at.desc())
        .limit(100)
    )).scalars().all()

    return [
        RemarqueOut(
            id=str(r.id),
            categorie=r.categorie,
            message=r.message,
            ecole=r.ecole,
            statut=r.statut,
            created_at=r.created_at.isoformat(),
        )
        for r in rows
    ]
