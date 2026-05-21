"""
Endpoints Admin — Suivi des séances par enseignant.
Fournit des statistiques agrégées pour le tableau de bord admin.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from app.core.deps import AdminUser, DB
from app.models.seance import Seance, SeanceStatus, RapportProf
from app.models.user import User, UserRole

router = APIRouter(prefix="/suivi-seances", tags=["Admin — Suivi Séances"])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _iso(dt: Optional[datetime]) -> Optional[str]:
    """Convertit un datetime en chaîne ISO 8601, ou None."""
    if dt is None:
        return None
    return dt.isoformat()


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Garantit que le datetime est timezone-aware (UTC)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _compute_score(
    total: int,
    terminees: int,
    seances_7j: int,
    taux_rapport: Optional[float],
    taux_completion: Optional[float],
) -> int:
    """
    Score d'engagement entre 0 et 100 :
    - 30 pts : activité récente (séances sur les 7 derniers jours, plafond 3)
    - 25 pts : taux de complétion
    - 25 pts : taux de rapport
    - 20 pts : volume total (plafonné à 20 séances)
    """
    score = 0.0
    score += min(seances_7j / 3.0, 1.0) * 30
    score += (taux_completion or 0.0) / 100.0 * 25
    score += (taux_rapport    or 0.0) / 100.0 * 25
    score += min(total / 20.0, 1.0) * 20
    return min(int(score), 100)


# ── Schémas de réponse ────────────────────────────────────────────────────────

class SuiviSeanceItem(BaseModel):
    teacher_id:         uuid.UUID
    name:               str
    title:              Optional[str]      = None
    phone:              Optional[str]      = None
    email:              Optional[str]      = None
    school_name:        Optional[str]      = None
    classes:            Optional[list[str]] = None
    total_seances:      int                = 0
    seances_terminees:  int                = 0
    seances_en_cours:   int                = 0
    derniere_activite:  Optional[str]      = None
    derniere_matiere:   Optional[str]      = None
    derniere_classe:    Optional[str]      = None
    dernier_status:     Optional[str]      = None
    taux_completion:    Optional[float]    = None
    taux_rapport:       Optional[float]    = None
    duree_moy_minutes:  Optional[float]    = None
    taux_presence:      Optional[float]    = None
    seances_7j:         int                = 0
    seances_30j:        int                = 0
    rapports_offline:   int                = 0
    total_rapports:     int                = 0
    jours_actifs:       int                = 0
    premiere_seance:    Optional[str]      = None
    derniere_connexion: Optional[str]      = None
    seances_planifiees: int                = 0
    seances_ad_hoc:     int                = 0
    score_engagement:   int                = 0

    model_config = {"from_attributes": True}


class SeanceDetailItem(BaseModel):
    id:                   str
    date_seance:          Optional[str]  = None
    started_at:           Optional[str]  = None
    finished_at:          Optional[str]  = None
    duree_minutes:        Optional[int]  = None
    matiere:              Optional[str]  = None
    classe:               str
    status:               str
    has_rapport:          bool           = False
    nb_eleves_presents:   Optional[int]  = None
    nb_eleves_total:      Optional[int]  = None
    planning_segment_id:  Optional[str]  = None
    soumis_offline:       Optional[bool] = None
    rapport_at:           Optional[str]  = None
    pauses:               Optional[list] = None
    total_paused_minutes: Optional[int]  = None


class TeacherDetailResponse(BaseModel):
    teacher_id:  uuid.UUID
    name:        str
    title:       Optional[str]      = None
    phone:       Optional[str]      = None
    email:       Optional[str]      = None
    school_name: Optional[str]      = None
    classes:     Optional[list[str]] = None
    seances:     list[SeanceDetailItem]


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("", response_model=list[SuiviSeanceItem])
async def list_suivi_seances(
    db: DB,
    _: AdminUser,
    session_id: Optional[uuid.UUID] = None,
    search:     Optional[str]       = None,
) -> list[SuiviSeanceItem]:
    """
    Retourne un agrégat par enseignant avec toutes les métriques d'engagement.
    Filtrable par session_id et par recherche sur le nom.
    """
    from app.models.school import School

    # ── 1. Récupérer les enseignants ──────────────────────────────────────────
    q_users = (
        select(User, School.name.label("school_name"))
        .outerjoin(School, School.id == User.school_id)
        .where(User.role == UserRole.enseignant)
        .order_by(User.name)
    )
    if search:
        q_users = q_users.where(User.name.ilike(f"%{search}%"))

    rows_users = (await db.execute(q_users)).all()
    if not rows_users:
        return []

    teacher_ids = [r.User.id for r in rows_users]

    # ── 2. Récupérer les séances ──────────────────────────────────────────────
    q_seances = (
        select(Seance)
        .where(Seance.teacher_id.in_(teacher_ids))
        .order_by(Seance.started_at.desc().nulls_last())
    )
    if session_id:
        q_seances = q_seances.where(Seance.session_id == session_id)

    all_seances: list[Seance] = (await db.execute(q_seances)).scalars().all()

    # ── 3. Récupérer les rapports ─────────────────────────────────────────────
    rapports_map: dict[uuid.UUID, RapportProf] = {}
    if all_seances:
        seance_ids = [s.id for s in all_seances]
        rapports = (await db.execute(
            select(RapportProf).where(RapportProf.seance_id.in_(seance_ids))
        )).scalars().all()
        rapports_map = {r.seance_id: r for r in rapports}

    # ── 4. Grouper les séances par enseignant ─────────────────────────────────
    seances_by_teacher: dict[uuid.UUID, list[Seance]] = {tid: [] for tid in teacher_ids}
    for s in all_seances:
        if s.teacher_id in seances_by_teacher:
            seances_by_teacher[s.teacher_id].append(s)

    now        = datetime.now(timezone.utc)
    cutoff_7j  = now - timedelta(days=7)
    cutoff_30j = now - timedelta(days=30)

    # ── 5. Construire les items ───────────────────────────────────────────────
    result: list[SuiviSeanceItem] = []
    for row in rows_users:
        user: User          = row.User
        school_name: Optional[str] = row.school_name
        seances             = seances_by_teacher.get(user.id, [])

        total      = len(seances)
        terminees  = sum(1 for s in seances if s.status == SeanceStatus.terminee)
        en_cours   = sum(1 for s in seances if s.status == SeanceStatus.en_cours)
        planifiees = sum(1 for s in seances if s.planning_segment_id is not None)
        ad_hoc     = total - planifiees

        seances_7j  = sum(
            1 for s in seances
            if _as_utc(s.started_at) is not None and _as_utc(s.started_at) >= cutoff_7j   # type: ignore[operator]
        )
        seances_30j = sum(
            1 for s in seances
            if _as_utc(s.started_at) is not None and _as_utc(s.started_at) >= cutoff_30j  # type: ignore[operator]
        )

        durees     = [s.duree_minutes for s in seances if s.duree_minutes is not None]
        duree_moy  = sum(durees) / len(durees) if durees else None

        total_rapports   = sum(1 for s in seances if s.id in rapports_map)
        rapports_offline = sum(
            1 for s in seances
            if s.id in rapports_map and rapports_map[s.id].soumis_en_offline
        )

        jours_actifs = len({
            _as_utc(s.started_at).date()  # type: ignore[union-attr]
            for s in seances
            if s.started_at is not None
        })

        taux_completion = (terminees / total * 100)    if total     > 0 else None
        taux_rapport    = (total_rapports / terminees * 100) if terminees > 0 else None

        presence_rates = [
            s.nb_eleves_presents / s.nb_eleves_total * 100
            for s in seances
            if s.nb_eleves_total and s.nb_eleves_total > 0 and s.nb_eleves_presents is not None
        ]
        taux_presence = sum(presence_rates) / len(presence_rates) if presence_rates else None

        # Dernière et première séance (déjà triées started_at DESC)
        derniere: Optional[Seance] = seances[0] if seances else None
        premiere_ts = min(
            (_as_utc(s.started_at) for s in seances if s.started_at is not None),
            default=None,
        )

        score = _compute_score(total, terminees, seances_7j, taux_rapport, taux_completion)

        result.append(SuiviSeanceItem(
            teacher_id=user.id,
            name=user.name,
            title=user.title,
            phone=user.phone,
            email=user.email,
            school_name=school_name,
            classes=user.classes,
            total_seances=total,
            seances_terminees=terminees,
            seances_en_cours=en_cours,
            derniere_activite=_iso(derniere.started_at) if derniere else None,
            derniere_matiere=derniere.matiere           if derniere else None,
            derniere_classe=derniere.classe             if derniere else None,
            dernier_status=derniere.status.value        if derniere else None,
            taux_completion=taux_completion,
            taux_rapport=taux_rapport,
            duree_moy_minutes=duree_moy,
            taux_presence=taux_presence,
            seances_7j=seances_7j,
            seances_30j=seances_30j,
            rapports_offline=rapports_offline,
            total_rapports=total_rapports,
            jours_actifs=jours_actifs,
            premiere_seance=_iso(premiere_ts),
            derniere_connexion=_iso(derniere.started_at) if derniere else None,
            seances_planifiees=planifiees,
            seances_ad_hoc=ad_hoc,
            score_engagement=score,
        ))

    return result


@router.get("/{teacher_id}", response_model=TeacherDetailResponse)
async def get_suivi_teacher(
    teacher_id: uuid.UUID,
    db: DB,
    _: AdminUser,
    session_id: Optional[uuid.UUID] = None,
) -> TeacherDetailResponse:
    """Détail complet des séances d'un enseignant spécifique."""
    from app.models.school import School

    row = (await db.execute(
        select(User, School.name.label("school_name"))
        .outerjoin(School, School.id == User.school_id)
        .where(User.id == teacher_id)
    )).first()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Enseignant introuvable.",
        )

    teacher: User          = row.User
    school_name: Optional[str] = row.school_name

    q = (
        select(Seance)
        .where(Seance.teacher_id == teacher_id)
        .order_by(Seance.date_seance.desc())
    )
    if session_id:
        q = q.where(Seance.session_id == session_id)

    seances: list[Seance] = (await db.execute(q)).scalars().all()

    rapports_map: dict[uuid.UUID, RapportProf] = {}
    if seances:
        rapports = (await db.execute(
            select(RapportProf).where(RapportProf.seance_id.in_([s.id for s in seances]))
        )).scalars().all()
        rapports_map = {r.seance_id: r for r in rapports}

    seance_details = [
        SeanceDetailItem(
            id=str(s.id),
            date_seance=_iso(s.date_seance),
            started_at=_iso(s.started_at),
            finished_at=_iso(s.finished_at),
            duree_minutes=s.duree_minutes,
            matiere=s.matiere,
            classe=s.classe,
            status=s.status.value,
            has_rapport=s.id in rapports_map,
            nb_eleves_presents=s.nb_eleves_presents,
            nb_eleves_total=s.nb_eleves_total,
            planning_segment_id=str(s.planning_segment_id) if s.planning_segment_id else None,
            soumis_offline=rapports_map[s.id].soumis_en_offline if s.id in rapports_map else None,
            rapport_at=_iso(rapports_map[s.id].created_at)      if s.id in rapports_map else None,
            pauses=s.pauses or [],
            total_paused_minutes=s.total_paused_minutes,
        )
        for s in seances
    ]

    return TeacherDetailResponse(
        teacher_id=teacher_id,
        name=teacher.name,
        title=teacher.title,
        phone=teacher.phone,
        email=teacher.email,
        school_name=school_name,
        classes=teacher.classes,
        seances=seance_details,
    )
