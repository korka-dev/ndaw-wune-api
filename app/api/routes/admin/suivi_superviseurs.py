"""
Endpoints Admin — Suivi des superviseurs.
Statistiques de présence des enseignants supervisés.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from app.core.deps import AdminUser, DB
from app.models.seance import RapportProf, Seance, SeanceStatus
from app.models.user import User, UserRole

router = APIRouter(prefix="/suivi-superviseurs", tags=["Admin — Suivi Superviseurs"])


# ── Schemas ────────────────────────────────────────────────────────────────────

class TeacherPresenceItem(BaseModel):
    teacher_id:        str
    name:              str
    title:             Optional[str]      = None
    phone:             Optional[str]      = None
    email:             Optional[str]      = None
    school_name:       Optional[str]      = None
    classes:           list[str]          = []
    presence_status:   str                        # "present" | "en_cours" | "absent"
    total_seances:     int                = 0
    seances_terminees: int                = 0
    seances_en_cours:  int                = 0
    derniere_seance:   Optional[datetime] = None
    derniere_matiere:  Optional[str]      = None
    derniere_classe:   Optional[str]      = None
    has_rapport:       bool               = False

    model_config = {"from_attributes": True}


class SuperviseurSuiviItem(BaseModel):
    superviseur_id: str
    name:           str
    title:          Optional[str] = None
    phone:          Optional[str] = None
    email:          Optional[str] = None
    school_name:    Optional[str] = None
    total_assignes: int           = 0
    presents:       int           = 0
    en_cours:       int           = 0
    absents:        int           = 0

    model_config = {"from_attributes": True}


class SuperviseurDetail(BaseModel):
    superviseur_id: str
    name:           str
    title:          Optional[str]          = None
    phone:          Optional[str]          = None
    email:          Optional[str]          = None
    school_name:    Optional[str]          = None
    teachers:       list[TeacherPresenceItem] = []

    model_config = {"from_attributes": True}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _parse_teacher_ids(classes: Optional[list[str]]) -> list[uuid.UUID]:
    """Extrait les UUIDs enseignants depuis le champ classes du superviseur."""
    ids: list[uuid.UUID] = []
    for s in (classes or []):
        try:
            ids.append(uuid.UUID(str(s)))
        except (ValueError, AttributeError):
            pass
    return ids


async def _teacher_presence(
    db,
    teacher: User,
    session_id: Optional[uuid.UUID],
) -> TeacherPresenceItem:
    """Calcule statut de présence + stats séances pour un enseignant."""
    today = date.today()

    filters = [Seance.teacher_id == teacher.id]
    if session_id:
        filters.append(Seance.session_id == session_id)

    seances = (await db.execute(
        select(Seance)
        .where(and_(*filters))
        .order_by(Seance.date_seance.desc())
    )).scalars().all()

    terminees  = [s for s in seances if s.status == SeanceStatus.terminee]
    en_cours_l = [s for s in seances if s.status == SeanceStatus.en_cours]

    # Présence : en_cours prime sur present
    if en_cours_l:
        presence = "en_cours"
    elif any(s.date_seance.date() == today for s in terminees):
        presence = "present"
    else:
        presence = "absent"

    derniere = seances[0] if seances else None

    # Rapport sur la dernière séance terminée
    has_rapport = False
    if terminees:
        rapport = (await db.execute(
            select(RapportProf).where(RapportProf.seance_id == terminees[0].id)
        )).scalar_one_or_none()
        has_rapport = rapport is not None

    return TeacherPresenceItem(
        teacher_id        = str(teacher.id),
        name              = teacher.name,
        title             = teacher.title,
        phone             = teacher.phone,
        email             = teacher.email,
        school_name       = teacher.school.name if teacher.school else None,
        classes           = teacher.classes or [],
        presence_status   = presence,
        total_seances     = len(seances),
        seances_terminees = len(terminees),
        seances_en_cours  = len(en_cours_l),
        derniere_seance   = derniere.date_seance if derniere else None,
        derniere_matiere  = derniere.matiere if derniere else None,
        derniere_classe   = derniere.classe if derniere else None,
        has_rapport       = has_rapport,
    )


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("", response_model=list[SuperviseurSuiviItem])
async def list_suivi_superviseurs(
    db:         DB,
    _:          AdminUser,
    session_id: Optional[uuid.UUID] = None,
    search:     Optional[str]       = None,
) -> list[SuperviseurSuiviItem]:
    """Liste tous les superviseurs avec les compteurs de présence de leurs enseignants."""
    q = (
        select(User)
        .where(
            or_(
                User.role == UserRole.superviseur,
                and_(User.role == UserRole.coordonnateur, User.title != "evaluateur"),
            )
        )
        .order_by(User.name)
    )
    if search:
        q = q.where(User.name.ilike(f"%{search}%"))

    superviseurs = (await db.execute(q)).scalars().all()

    today = date.today()
    result: list[SuperviseurSuiviItem] = []

    for sup in superviseurs:
        teacher_ids    = _parse_teacher_ids(sup.classes)
        total_assignes = len(teacher_ids)
        presents       = 0
        en_cours_count = 0

        if teacher_ids:
            # Séances du jour pour ces enseignants (en une seule requête)
            day_filters = [
                Seance.teacher_id.in_(teacher_ids),
                func.date(Seance.date_seance) == today,
            ]
            if session_id:
                day_filters.append(Seance.session_id == session_id)

            rows = (await db.execute(
                select(Seance.teacher_id, Seance.status).where(and_(*day_filters))
            )).all()

            seen_en_cours: set[uuid.UUID] = set()
            seen_present:  set[uuid.UUID] = set()
            for tid, st in rows:
                if st == SeanceStatus.en_cours:
                    seen_en_cours.add(tid)
                elif st == SeanceStatus.terminee:
                    seen_present.add(tid)

            en_cours_count = len(seen_en_cours)
            presents       = len(seen_present - seen_en_cours)

        result.append(SuperviseurSuiviItem(
            superviseur_id = str(sup.id),
            name           = sup.name,
            title          = sup.title,
            phone          = sup.phone,
            email          = sup.email,
            school_name    = sup.school.name if sup.school else None,
            total_assignes = total_assignes,
            presents       = presents,
            en_cours       = en_cours_count,
            absents        = max(0, total_assignes - presents - en_cours_count),
        ))

    return result


@router.get("/{superviseur_id}", response_model=SuperviseurDetail)
async def get_suivi_superviseur(
    superviseur_id: uuid.UUID,
    db:             DB,
    _:              AdminUser,
    session_id:     Optional[uuid.UUID] = None,
) -> SuperviseurDetail:
    """Détail d'un superviseur : infos + présence de chaque enseignant assigné."""
    sup = (await db.execute(
        select(User).where(User.id == superviseur_id)
    )).scalar_one_or_none()

    if sup is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Superviseur introuvable.")

    teacher_ids = _parse_teacher_ids(sup.classes)

    teachers: list[TeacherPresenceItem] = []
    if teacher_ids:
        teacher_users = (await db.execute(
            select(User)
            .where(User.id.in_(teacher_ids))
            .order_by(User.name)
        )).scalars().all()

        for t in teacher_users:
            teachers.append(await _teacher_presence(db, t, session_id))

    return SuperviseurDetail(
        superviseur_id = str(sup.id),
        name           = sup.name,
        title          = sup.title,
        phone          = sup.phone,
        email          = sup.email,
        school_name    = sup.school.name if sup.school else None,
        teachers       = teachers,
    )
