"""
Endpoints Admin — Suivi des séances par enseignant.
Fournit des statistiques agrégées pour le tableau de bord admin.
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select

from app.core.deps import AdminUser, DB
from app.models.seance import Seance, SeanceStatus
from app.models.session import ProgramSession
from app.models.user import User, UserRole

router = APIRouter(prefix="/suivi-seances", tags=["Admin — Suivi Séances"])


# ── Schémas de réponse ────────────────────────────────────────────────────────

class SuiviSeanceItem(BaseModel):
    teacher_id:     uuid.UUID
    teacher_name:   str
    teacher_phone:  Optional[str]
    school_name:    Optional[str]
    total_seances:  int
    seances_terminees: int
    duree_totale_minutes: Optional[int]

    model_config = {"from_attributes": True}


class SuiviSeanceDetail(BaseModel):
    teacher_id:    uuid.UUID
    teacher_name:  str
    seances: list[dict]  # liste détaillée des séances

    model_config = {"from_attributes": True}


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("", response_model=list[SuiviSeanceItem])
async def list_suivi_seances(
    db: DB,
    _: AdminUser,
    session_id: Optional[uuid.UUID] = None,
    search:     Optional[str]       = None,
) -> list[SuiviSeanceItem]:
    """
    Retourne un agrégat par enseignant :
    - nombre total de séances
    - nombre de séances terminées
    - durée totale enregistrée (minutes)
    Filtrable par session_id et par recherche sur le nom.
    """
    from app.models.school import School

    q = (
        select(
            User.id.label("teacher_id"),
            User.name.label("teacher_name"),
            User.phone.label("teacher_phone"),
            School.name.label("school_name"),
            func.count(Seance.id).label("total_seances"),
            func.sum(
                func.cast(Seance.status == SeanceStatus.terminee, func.Integer())
            ).label("seances_terminees"),
            func.sum(Seance.duree_minutes).label("duree_totale_minutes"),
        )
        .select_from(User)
        .outerjoin(Seance, Seance.teacher_id == User.id)
        .outerjoin(School, School.id == User.school_id)
        .where(User.role == UserRole.enseignant)
        .group_by(User.id, User.name, User.phone, School.name)
        .order_by(User.name)
    )

    if session_id:
        q = q.where(Seance.session_id == session_id)
    if search:
        q = q.where(User.name.ilike(f"%{search}%"))

    rows = (await db.execute(q)).all()
    return [
        SuiviSeanceItem(
            teacher_id=r.teacher_id,
            teacher_name=r.teacher_name,
            teacher_phone=r.teacher_phone,
            school_name=r.school_name,
            total_seances=r.total_seances or 0,
            seances_terminees=r.seances_terminees or 0,
            duree_totale_minutes=r.duree_totale_minutes,
        )
        for r in rows
    ]


@router.get("/{teacher_id}", response_model=SuiviSeanceDetail)
async def get_suivi_teacher(
    teacher_id: uuid.UUID,
    db: DB,
    _: AdminUser,
    session_id: Optional[uuid.UUID] = None,
) -> SuiviSeanceDetail:
    """Détail des séances d'un enseignant spécifique."""
    result = await db.execute(select(User).where(User.id == teacher_id))
    teacher = result.scalar_one_or_none()
    if teacher is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Enseignant introuvable.")

    q = select(Seance).where(Seance.teacher_id == teacher_id).order_by(Seance.date_seance.desc())
    if session_id:
        q = q.where(Seance.session_id == session_id)

    seances = (await db.execute(q)).scalars().all()
    return SuiviSeanceDetail(
        teacher_id=teacher_id,
        teacher_name=teacher.name,
        seances=[
            {
                "id":             str(s.id),
                "classe":         s.classe,
                "matiere":        s.matiere,
                "date_seance":    s.date_seance.isoformat() if s.date_seance else None,
                "status":         s.status.value,
                "duree_minutes":  s.duree_minutes,
                "started_at":     s.started_at.isoformat() if s.started_at else None,
                "finished_at":    s.finished_at.isoformat() if s.finished_at else None,
            }
            for s in seances
        ],
    )
