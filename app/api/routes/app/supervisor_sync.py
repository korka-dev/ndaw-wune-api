"""Endpoint App mobile — Synchronisation des superviseurs de terrain.

Retourne les données nécessaires au superviseur pour travailler hors ligne :
  - son profil
  - la liste des enseignants qui lui sont assignés (via sup.classes)
  - la session active du programme

Accessible uniquement aux utilisateurs avec le rôle 'superviseur'.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select, func

from app.core.deps import DB, SuperviseurUser
from app.models.evaluation_competence import EvaluationCompetence
from app.models.rapport_libelle import RapportLibelle
from app.models.rapport_question import RapportQuestion
from app.models.rapport_journalier import RapportJournalier
from app.models.session import ProgramSession, SessionStatus
from app.models.user import User
from app.schemas.sync import SyncRapportQuestion
from app.services.progression_service import get_config_for
from app.services.supervisor_service import get_supervised_teacher_ids

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


class SupSchoolInfo(BaseModel):
    id:     str
    name:   str
    region: Optional[str] = None
    city:   Optional[str] = None
    langue: Optional[str] = None


class EvaluationCompetenceItem(BaseModel):
    id:    str
    label: str
    code:  str
    ordre: int

    model_config = {"from_attributes": True}


class SupervisorSyncPayload(BaseModel):
    synced_at:      str
    profile:        SupervisorProfile
    school:         Optional[SupSchoolInfo] = None
    assigned_teachers: list[AssignedTeacher]
    active_session: Optional[ActiveSessionInfo] = None
    evaluation_competences: list[EvaluationCompetenceItem] = []
    rapport_questions: list[SyncRapportQuestion] = []
    rapport_libelles: dict[str, str] = {}
    nb_semaines: int = 10
    nb_jours:    int = 3


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

    # ── École de rattachement (relation chargée en selectin sur User) ─────────
    school_info: SupSchoolInfo | None = None
    if current_user.school:
        school_info = SupSchoolInfo(
            id=str(current_user.school.id),
            name=current_user.school.name,
            region=current_user.school.region,
            city=current_user.school.city,
            langue=current_user.school.langue,
        )

    # ── Enseignants supervisés (assignation explicite, sinon école) ──────────
    assigned_teachers: list[AssignedTeacher] = []
    teacher_uuids = await get_supervised_teacher_ids(db, current_user)

    # Date du dernier RAPPORT JOURNALIER de chaque enseignant, en une requête.
    #
    # C'est bien `rapports_journalier` qu'il faut interroger, et non
    # `rapports_prof` : ce dernier ne contient que les comptes rendus de fin de
    # séance (flux minuteur), que beaucoup d'enseignants ne produisent jamais
    # selon leur `app_access`. Le superviseur voyait donc « Aucun rapport
    # soumis » en face de tuteurs parfaitement à jour de leurs rapports.
    #
    # On retient `date_rapport` (le jour de classe concerné) plutôt que
    # `created_at` : un rapport rédigé hors-ligne et transmis deux jours plus
    # tard doit compter pour le jour où la classe a eu lieu.
    last_rapport_map: dict[uuid.UUID, date] = {}
    if teacher_uuids:
        last_rapports = (
            await db.execute(
                select(
                    RapportJournalier.teacher_id,
                    func.max(RapportJournalier.date_rapport).label("last_date"),
                )
                .where(RapportJournalier.teacher_id.in_(teacher_uuids))
                .group_by(RapportJournalier.teacher_id)
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

    # ── Questions complémentaires du rapport superviseur (configurées par l'admin) ──
    # Ne renvoie que les questions destinées au superviseur (ou à "tous").
    questions_rows = (
        await db.execute(
            select(RapportQuestion)
            .where(
                RapportQuestion.active.is_(True),
                RapportQuestion.cible.in_(["superviseur", "tous"]),
            )
            .order_by(RapportQuestion.ordre, RapportQuestion.created_at)
        )
    ).scalars().all()
    rapport_questions = [SyncRapportQuestion.model_validate(q) for q in questions_rows]

    # ── Libellés éditables des champs fixes du rapport superviseur ───────────
    libelles_rows = (
        await db.execute(
            select(RapportLibelle).where(RapportLibelle.cible == "superviseur")
        )
    ).scalars().all()
    rapport_libelles = {l.cle: l.texte for l in libelles_rows}

    # ── Nombre de semaines/jours du programme (même config que la partie tuteur) ──
    nb_semaines, nb_jours = await get_config_for(
        db, None, session.id if session else None
    )

    return SupervisorSyncPayload(
        synced_at=datetime.now(timezone.utc).isoformat(),
        profile=profile,
        school=school_info,
        assigned_teachers=assigned_teachers,
        active_session=active_session,
        evaluation_competences=evaluation_competences,
        rapport_questions=rapport_questions,
        rapport_libelles=rapport_libelles,
        nb_semaines=nb_semaines,
        nb_jours=nb_jours,
    )
