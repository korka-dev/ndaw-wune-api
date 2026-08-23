"""
Endpoints Admin — Suivi des superviseurs.
Statistiques de présence des enseignants supervisés.
"""
from __future__ import annotations

import csv
import io
import uuid
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from app.core.deps import AdminUser, DB
from app.core.export_utils import sanitize_cell
from app.models.rapport_journalier import RapportJournalier
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


class TuteurEnAlerte(BaseModel):
    teacher_id:     str
    name:           str
    rapports_manques: int   # nombre de jours sans rapport (7 derniers jours)


class SuperviseurSuiviItem(BaseModel):
    superviseur_id:     str
    name:               str
    title:              Optional[str]       = None
    phone:              Optional[str]       = None
    email:              Optional[str]       = None
    school_name:        Optional[str]       = None
    total_assignes:     int                 = 0
    presents:           int                 = 0
    en_cours:           int                 = 0
    absents:            int                 = 0
    # Alerte rapports : tuteurs avec 3+ jours sans rapport sur les 7 derniers jours
    tuteurs_en_alerte:  list[TuteurEnAlerte] = []

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

def _working_days(nb_days: int = 7) -> list[date]:
    """Jours ouvrés (lun–sam) des `nb_days` derniers jours calendaires."""
    from datetime import timedelta
    today = date.today()
    working_days: list[date] = []
    d = today
    while len(working_days) < nb_days:
        if d.weekday() != 6:  # pas dimanche
            working_days.append(d)
        d -= timedelta(days=1)
    return working_days


async def _count_missed_reports_bulk(
    db, teacher_ids: list[uuid.UUID], nb_days: int = 7,
) -> dict[uuid.UUID, int]:
    """
    Pour chaque enseignant, compte le nombre de jours ouvrés (lun–sam) des
    `nb_days` derniers jours sans rapport journalier soumis.

    Une seule requête pour tous les enseignants (au lieu d'une requête par
    enseignant) — c'était la source principale de lenteur du dashboard.
    """
    if not teacher_ids:
        return {}

    working_days = _working_days(nb_days)

    rows = (await db.execute(
        select(RapportJournalier.teacher_id, RapportJournalier.date_rapport).where(
            RapportJournalier.teacher_id.in_(teacher_ids),
            RapportJournalier.date_rapport.in_(working_days),
        )
    )).all()

    sent_by_teacher: dict[uuid.UUID, set[date]] = {}
    for tid, d in rows:
        sent_by_teacher.setdefault(tid, set()).add(d)

    return {
        tid: sum(1 for d in working_days if d not in sent_by_teacher.get(tid, set()))
        for tid in teacher_ids
    }


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

async def _build_suivi_superviseurs(
    db,
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

    # ── Pré-calcul groupé : évite une requête par enseignant supervisé ─────────
    # (c'était la source principale de lenteur du dashboard : N superviseurs ×
    # M enseignants = N×M allers-retours DB séquentiels)

    all_teacher_ids: set[uuid.UUID] = set()
    teacher_ids_by_sup: dict[uuid.UUID, list[uuid.UUID]] = {}
    for sup in superviseurs:
        tids = _parse_teacher_ids(sup.classes)
        teacher_ids_by_sup[sup.id] = tids
        all_teacher_ids.update(tids)

    all_teacher_ids_list = list(all_teacher_ids)

    # Séances du jour pour tous les enseignants supervisés, en une seule requête
    day_status_by_teacher: dict[uuid.UUID, set[SeanceStatus]] = {}
    if all_teacher_ids_list:
        day_filters = [
            Seance.teacher_id.in_(all_teacher_ids_list),
            func.date(Seance.date_seance) == today,
        ]
        if session_id:
            day_filters.append(Seance.session_id == session_id)

        rows = (await db.execute(
            select(Seance.teacher_id, Seance.status).where(and_(*day_filters))
        )).all()
        for tid, st in rows:
            day_status_by_teacher.setdefault(tid, set()).add(st)

    # Rapports manqués (3+ sur 7 jours) pour tous les enseignants, en une requête
    missed_by_teacher = await _count_missed_reports_bulk(db, all_teacher_ids_list)

    # Noms des enseignants (pour l'alerte), en une seule requête
    teacher_names: dict[uuid.UUID, str] = {}
    if all_teacher_ids_list:
        teacher_names = dict((await db.execute(
            select(User.id, User.name).where(User.id.in_(all_teacher_ids_list))
        )).all())

    result: list[SuperviseurSuiviItem] = []

    for sup in superviseurs:
        teacher_ids    = teacher_ids_by_sup[sup.id]
        total_assignes = len(teacher_ids)
        presents       = 0
        en_cours_count = 0

        if teacher_ids:
            seen_en_cours: set[uuid.UUID] = set()
            seen_present:  set[uuid.UUID] = set()
            for tid in teacher_ids:
                statuses = day_status_by_teacher.get(tid, set())
                if SeanceStatus.en_cours in statuses:
                    seen_en_cours.add(tid)
                elif SeanceStatus.terminee in statuses:
                    seen_present.add(tid)

            en_cours_count = len(seen_en_cours)
            presents       = len(seen_present - seen_en_cours)

        # Tuteurs en alerte (3+ rapports manquants sur 7 jours)
        tuteurs_en_alerte: list[TuteurEnAlerte] = [
            TuteurEnAlerte(
                teacher_id=str(tid),
                name=teacher_names.get(tid, "—"),
                rapports_manques=missed_by_teacher.get(tid, 0),
            )
            for tid in teacher_ids
            if missed_by_teacher.get(tid, 0) >= 3 and tid in teacher_names
        ]

        result.append(SuperviseurSuiviItem(
            superviseur_id    = str(sup.id),
            name              = sup.name,
            title             = sup.title,
            phone             = sup.phone,
            email             = sup.email,
            school_name       = sup.school.name if sup.school else None,
            total_assignes    = total_assignes,
            presents          = presents,
            en_cours          = en_cours_count,
            absents           = max(0, total_assignes - presents - en_cours_count),
            tuteurs_en_alerte = tuteurs_en_alerte,
        ))

    return result


@router.get("", response_model=list[SuperviseurSuiviItem])
async def list_suivi_superviseurs(
    db:         DB,
    _:          AdminUser,
    session_id: Optional[uuid.UUID] = None,
    search:     Optional[str]       = None,
) -> list[SuperviseurSuiviItem]:
    return await _build_suivi_superviseurs(db, session_id=session_id, search=search)


@router.get("/export/csv")
async def export_suivi_superviseurs_csv(
    db:         DB,
    _:          AdminUser,
    session_id: Optional[uuid.UUID] = None,
    search:     Optional[str]       = None,
) -> StreamingResponse:
    """Export CSV du suivi des superviseurs (compteurs de présence de leurs enseignants)."""
    items = await _build_suivi_superviseurs(db, session_id=session_id, search=search)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Superviseur", "École", "Assignés", "Présents", "En cours", "Absents", "Tuteurs en alerte"])
    for it in items:
        writer.writerow([sanitize_cell(v) for v in (
            it.name,
            it.school_name or "",
            it.total_assignes,
            it.presents,
            it.en_cours,
            it.absents,
            ", ".join(t.name for t in it.tuteurs_en_alerte),
        )])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=suivi_superviseurs.csv"},
    )


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
