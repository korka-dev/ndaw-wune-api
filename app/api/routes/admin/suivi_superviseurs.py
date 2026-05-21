"""
Endpoints Admin — Suivi des superviseurs.
Statistiques sur l'activité des superviseurs / coordonnateurs.
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from app.core.deps import AdminUser, DB
from app.models.school import School
from app.models.seance import Seance, SeanceStatus, RapportProf
from app.models.user import User, UserRole

router = APIRouter(prefix="/suivi-superviseurs", tags=["Admin — Suivi Superviseurs"])


class SuiviSuperviseurItem(BaseModel):
    superviseur_id:  uuid.UUID
    name:            str
    title:           Optional[str] = None
    phone:           Optional[str] = None
    email:           Optional[str] = None
    school_name:     Optional[str] = None
    total_assignes:  int = 0
    presents:        int = 0
    en_cours:        int = 0
    absents:         int = 0

    model_config = {"from_attributes": True}


class SuiviSuperviseurDetail(BaseModel):
    superviseur_id: uuid.UUID
    name:           str
    title:          Optional[str] = None
    phone:          Optional[str] = None
    email:          Optional[str] = None
    school_name:    Optional[str] = None
    teachers:       list[dict] = []

    model_config = {"from_attributes": True}


@router.get("", response_model=list[SuiviSuperviseurItem])
async def list_suivi_superviseurs(
    db: DB,
    _: AdminUser,
    session_id: Optional[uuid.UUID] = None,
    search:     Optional[str]       = None,
) -> list[SuiviSuperviseurItem]:
    """Liste tous les superviseurs avec les statistiques de présence de leurs enseignants."""
    q = (
        select(User, School.name.label("school_name"))
        .outerjoin(School, School.id == User.school_id)
        .where(User.role == UserRole.coordonnateur, User.title != "evaluateur")
        .order_by(User.name)
    )
    if search:
        q = q.where(User.name.ilike(f"%{search}%"))

    rows = (await db.execute(q)).all()
    if not rows:
        return []

    # 1. Collecter tous les enseignants assignés à ces superviseurs
    all_teacher_ids = []
    for r in rows:
        sup = r.User
        if sup.classes:
            for t_id_str in sup.classes:
                try:
                    all_teacher_ids.append(uuid.UUID(t_id_str))
                except ValueError:
                    continue

    # 2. Récupérer toutes les séances de ces enseignants (filtrées par session_id)
    seances_by_teacher = {}
    if all_teacher_ids:
        q_seances = select(Seance).where(Seance.teacher_id.in_(all_teacher_ids))
        if session_id:
            q_seances = q_seances.where(Seance.session_id == session_id)
        
        seances = (await db.execute(q_seances)).scalars().all()
        for s in seances:
            if s.teacher_id not in seances_by_teacher:
                seances_by_teacher[s.teacher_id] = []
            seances_by_teacher[s.teacher_id].append(s)

    # 3. Calculer le statut de chaque enseignant
    # status: "present" | "en_cours" | "absent"
    teacher_status = {}
    for t_id in all_teacher_ids:
        t_seances = seances_by_teacher.get(t_id, [])
        en_cours = sum(1 for s in t_seances if s.status == SeanceStatus.en_cours)
        terminees = sum(1 for s in t_seances if s.status == SeanceStatus.terminee)
        if en_cours > 0:
            teacher_status[t_id] = "en_cours"
        elif terminees > 0:
            teacher_status[t_id] = "present"
        else:
            teacher_status[t_id] = "absent"

    # 4. Construire la liste de suivi pour les superviseurs
    result = []
    for r in rows:
        sup = r.User
        sup_school_name = r.school_name

        presents_cnt = 0
        en_cours_cnt = 0
        absents_cnt = 0
        total_cnt = 0

        if sup.classes:
            for t_id_str in sup.classes:
                try:
                    t_id = uuid.UUID(t_id_str)
                    status_val = teacher_status.get(t_id, "absent")
                    total_cnt += 1
                    if status_val == "en_cours":
                        en_cours_cnt += 1
                    elif status_val == "present":
                        presents_cnt += 1
                    else:
                        absents_cnt += 1
                except ValueError:
                    continue

        result.append(
            SuiviSuperviseurItem(
                superviseur_id=sup.id,
                name=sup.name,
                title=sup.title,
                phone=sup.phone,
                email=sup.email,
                school_name=sup_school_name,
                total_assignes=total_cnt,
                presents=presents_cnt,
                en_cours=en_cours_cnt,
                absents=absents_cnt,
            )
        )

    return result


@router.get("/{superviseur_id}", response_model=SuiviSuperviseurDetail)
async def get_suivi_superviseur(
    superviseur_id: uuid.UUID,
    db: DB,
    _: AdminUser,
    session_id: Optional[uuid.UUID] = None,
) -> SuiviSuperviseurDetail:
    """Détail complet d'un superviseur avec la liste de ses enseignants et leur état."""
    # 1. Récupérer le superviseur
    row = (await db.execute(
        select(User, School.name.label("school_name"))
        .outerjoin(School, School.id == User.school_id)
        .where(User.id == superviseur_id)
    )).first()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Superviseur introuvable.",
        )

    sup = row.User
    sup_school_name = row.school_name

    # 2. Récupérer les enseignants assignés
    teachers = []
    teacher_uuids = []
    if sup.classes:
        for t_id_str in sup.classes:
            try:
                teacher_uuids.append(uuid.UUID(t_id_str))
            except ValueError:
                continue

    if teacher_uuids:
        q_teachers = (
            select(User, School.name.label("school_name"))
            .outerjoin(School, School.id == User.school_id)
            .where(User.id.in_(teacher_uuids))
            .order_by(User.name)
        )
        teacher_rows = (await db.execute(q_teachers)).all()

        # Récupérer les séances de ces enseignants (filtrées par session_id)
        q_seances = (
            select(Seance)
            .where(Seance.teacher_id.in_(teacher_uuids))
            .order_by(Seance.started_at.desc().nulls_last())
        )
        if session_id:
            q_seances = q_seances.where(Seance.session_id == session_id)
        
        all_seances = (await db.execute(q_seances)).scalars().all()

        # Récupérer les rapports associés
        rapports_map = {}
        if all_seances:
            seance_ids = [s.id for s in all_seances]
            rapports = (await db.execute(
                select(RapportProf).where(RapportProf.seance_id.in_(seance_ids))
            )).scalars().all()
            rapports_map = {r.seance_id: r for r in rapports}

        # Grouper les séances par enseignant
        seances_by_teacher = {tid: [] for tid in teacher_uuids}
        for s in all_seances:
            if s.teacher_id in seances_by_teacher:
                seances_by_teacher[s.teacher_id].append(s)

        # Construire les objets enseignants avec leur état de présence
        for tr in teacher_rows:
            t = tr.User
            t_school_name = tr.school_name
            t_seances = seances_by_teacher.get(t.id, [])

            total = len(t_seances)
            terminees = sum(1 for s in t_seances if s.status == SeanceStatus.terminee)
            en_cours = sum(1 for s in t_seances if s.status == SeanceStatus.en_cours)

            if en_cours > 0:
                presence_status = "en_cours"
            elif terminees > 0:
                presence_status = "present"
            else:
                presence_status = "absent"

            derniere = t_seances[0] if t_seances else None
            has_rapport = any(s.id in rapports_map for s in t_seances)

            teachers.append({
                "teacher_id":        str(t.id),
                "name":              t.name,
                "title":             t.title,
                "phone":             t.phone,
                "email":             t.email,
                "school_name":       t_school_name,
                "classes":           t.classes or [],
                "presence_status":   presence_status,
                "total_seances":     total,
                "seances_terminees": terminees,
                "seances_en_cours":  en_cours,
                "derniere_seance":   derniere.started_at.isoformat() if (derniere and  derniere.started_at) else None,
                "derniere_matiere":  derniere.matiere if  derniere else None,
                "derniere_classe":   derniere.classe if  derniere else None,
                "has_rapport":       has_rapport,
            })

    return SuiviSuperviseurDetail(
        superviseur_id=sup.id,
        name=sup.name,
        title=sup.title,
        phone=sup.phone,
        email=sup.email,
        school_name=sup_school_name,
        teachers=teachers,
    )
