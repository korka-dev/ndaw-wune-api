"""
Endpoints Admin — Suivi des superviseurs.
Statistiques sur l'activité des superviseurs / coordonnateurs.
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, or_, select

from app.core.deps import AdminUser, DB
from app.models.user import User, UserRole

router = APIRouter(prefix="/suivi-superviseurs", tags=["Admin — Suivi Superviseurs"])


class SuiviSuperviseurItem(BaseModel):
    superviseur_id:   uuid.UUID
    superviseur_name: str
    superviseur_phone: Optional[str]
    nb_enseignants:   int     # nombre d'enseignants assignés

    model_config = {"from_attributes": True}


class SuiviSuperviseurDetail(BaseModel):
    superviseur_id:   uuid.UUID
    superviseur_name: str
    enseignants:      list[dict]

    model_config = {"from_attributes": True}


@router.get("", response_model=list[SuiviSuperviseurItem])
async def list_suivi_superviseurs(
    db: DB,
    _: AdminUser,
    session_id: Optional[uuid.UUID] = None,  # ignoré pour l'instant, prévu pour l'évolution
    search:     Optional[str]       = None,
) -> list[SuiviSuperviseurItem]:
    """Liste tous les superviseurs avec le nombre d'enseignants qui leur sont assignés."""
    # Inclut le nouveau rôle 'superviseur' ET les anciens 'coordonnateur' non-évaluateurs
    # (rétrocompatibilité avant la migration a9f1b2c3d4e5)
    q = (
        select(User)
        .where(
            or_(
                User.role == UserRole.superviseur,
                (User.role == UserRole.coordonnateur) & (User.title != "evaluateur"),
            )
        )
        .order_by(User.name)
    )
    if search:
        q = q.where(User.name.ilike(f"%{search}%"))

    superviseurs = (await db.execute(q)).scalars().all()
    return [
        SuiviSuperviseurItem(
            superviseur_id=s.id,
            superviseur_name=s.name,
            superviseur_phone=s.phone,
            nb_enseignants=len(s.classes or []),
        )
        for s in superviseurs
    ]


@router.get("/{superviseur_id}", response_model=SuiviSuperviseurDetail)
async def get_suivi_superviseur(
    superviseur_id: uuid.UUID,
    db: DB,
    _: AdminUser,
    session_id: Optional[uuid.UUID] = None,
) -> SuiviSuperviseurDetail:
    """Détail d'un superviseur avec la liste de ses enseignants assignés."""
    result = await db.execute(select(User).where(User.id == superviseur_id))
    sup = result.scalar_one_or_none()
    if sup is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Superviseur introuvable.")

    # Récupérer les enseignants assignés (stockés dans sup.classes comme IDs)
    enseignants: list[dict] = []
    if sup.classes:
        for teacher_id_str in sup.classes:
            try:
                teacher_uuid = uuid.UUID(teacher_id_str)
                t_res = await db.execute(select(User).where(User.id == teacher_uuid))
                teacher = t_res.scalar_one_or_none()
                if teacher:
                    enseignants.append({
                        "id":    str(teacher.id),
                        "name":  teacher.name,
                        "phone": teacher.phone,
                        "email": teacher.email,
                    })
            except ValueError:
                continue

    return SuiviSuperviseurDetail(
        superviseur_id=superviseur_id,
        superviseur_name=sup.name,
        enseignants=enseignants,
    )
