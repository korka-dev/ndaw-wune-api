"""Endpoint App mobile — Synchronisation des superviseurs de terrain.

Retourne les données nécessaires au superviseur pour travailler hors ligne :
  - son profil
  - la liste des enseignants qui lui sont assignés (via sup.classes)
  - la session active du programme

Accessible uniquement aux utilisateurs avec le rôle 'superviseur'.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select, func

from app.core.deps import DB, SuperviseurUser
from app.models.evaluation_competence import EvaluationCompetence
from app.models.seance import RapportProf
from app.models.session import ProgramSession, SessionStatus
from app.models.user import User

router = APIRouter(prefix="/supervisor", tags=["App — Superviseur"])


# ── Schémas de réponse ────────────────────────────────────────────────────────

class SupervisorProfile(BaseModel):
    id:    str
    name:  str
    phone: Optional[str] = None
    email: Optional[str] = None
    role:  str

    model_config = {"from_attributes": True}


class AssignedTeacher(BaseModel):
    id:    str
    name:  str
    phone: Optional[str] = None
    email: Optional[str] = None
    last_rapport_date: Optional[str] = None

    model_config = {"from_attributes": True}


class ActiveSessionInfo(BaseModel):
    id:         str
    nom:        str
    date_debut: str
    date_fin:   str

    model_config = {"from_attributes": True}


class EvaluationCompetenceItem(BaseModel):
    id:    str
    label: str
    code:  str
    ordre: int

    model_config = {"from_attributes": True}


class SupervisorSyncPayload(BaseModel):
    synced_at:      str
    profile:        SupervisorProfile
    assigned_teachers: list[AssignedTeacher]
    active_session: Optional[ActiveSessionInfo] = None
    evaluation_competences: list[EvaluationCompetenceItem] = []


# ── Route ─────────────────────────────────────────────────────────────────────

@router.get("/sync", response_model=SupervisorSyncPayload)
async def supervisor_sync(current_user: SuperviseurUser, db: DB) -> SupervisorSyncPayload:
    """
    Télécharge les données de synchronisation pour un superviseur de terrain.
    Appelé au login et à chaque retour en ligne.
    """
    # ── Profil du superviseur ─────────────────────────────────────────────────
    profile = SupervisorProfile(
        id=str(current_user.id),
        name=current_user.name,
        phone=current_user.phone,
        email=current_user.email,
        role=current_user.role.value,
    )

    # ── Enseignants assignés (stockés dans supervisor.classes comme UUIDs) ────
    assigned_teachers: list[AssignedTeacher] = []
    teacher_uuids: list[uuid.UUID] = []
    if current_user.classes:
        for id_str in current_user.classes:
            try:
                teacher_uuids.append(uuid.UUID(id_str))
            except ValueError:
                continue

    # Récupérer la date du dernier rapport pour chaque enseignant en un seul query
    last_rapport_map: dict[uuid.UUID, datetime] = {}
    if teacher_uuids:
        last_rapports = (
            await db.execute(
                select(
                    RapportProf.teacher_id,
                    func.max(RapportProf.created_at).label("last_date"),
                )
                .where(RapportProf.teacher_id.in_(teacher_uuids))
                .group_by(RapportProf.teacher_id)
            )
        ).all()
        for row in last_rapports:
            last_rapport_map[row.teacher_id] = row.last_date

    for teacher_uuid in teacher_uuids:
        result = await db.execute(select(User).where(User.id == teacher_uuid))
        teacher = result.scalar_one_or_none()
        if teacher:
            last_date = last_rapport_map.get(teacher_uuid)
            assigned_teachers.append(
                AssignedTeacher(
                    id=str(teacher.id),
                    name=teacher.name,
                    phone=teacher.phone,
                    email=teacher.email,
                    last_rapport_date=last_date.isoformat() if last_date else None,
                )
            )

    # ── Session active ────────────────────────────────────────────────────────
    active_session: ActiveSessionInfo | None = None
    session = (
        await db.execute(
            select(ProgramSession)
            .where(ProgramSession.status == SessionStatus.active)
            .order_by(ProgramSession.date_debut.desc())
            .limit(1)
        )
    ).scalars().first()

    if session:
        active_session = ActiveSessionInfo(
            id=str(session.id),
            nom=session.name,
            date_debut=session.date_debut.isoformat(),
            date_fin=session.date_fin.isoformat(),
        )

    # ── Compétences d'évaluation (configurées par l'admin) ───────────────────
    competences_rows = (
        await db.execute(
            select(EvaluationCompetence)
            .where(EvaluationCompetence.active.is_(True))
            .order_by(EvaluationCompetence.ordre, EvaluationCompetence.created_at)
        )
    ).scalars().all()
    evaluation_competences = [
        EvaluationCompetenceItem(id=str(c.id), label=c.label, code=c.code, ordre=c.ordre)
        for c in competences_rows
    ]

    return SupervisorSyncPayload(
        synced_at=datetime.now(timezone.utc).isoformat(),
        profile=profile,
        assigned_teachers=assigned_teachers,
        active_session=active_session,
        evaluation_competences=evaluation_competences,
    )
