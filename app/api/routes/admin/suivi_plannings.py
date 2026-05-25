"""
Endpoints Admin — Suivi des plannings par segment.
Pour chaque créneau planifié, retourne les séances réellement effectuées
(started_at / finished_at réels vs horaires planifiés), permettant
de mesurer l'écart et de suivre l'exécution du planning en temps réel.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone, date, time as dt_time
from typing import Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select

from app.core.deps import AdminUser, DB
from app.models.planning import PlanningSegment
from app.models.seance import Seance, SeanceStatus, RapportProf
from app.models.user import User

router = APIRouter(prefix="/suivi-plannings", tags=["Admin — Suivi Plannings"])

JOURS_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _iso(dt: Optional[datetime]) -> Optional[str]:
    """Datetime → ISO 8601 ou None."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _time_str(t: Optional[dt_time]) -> Optional[str]:
    """time → 'HH:MM' ou None."""
    if t is None:
        return None
    return t.strftime("%H:%M")


def _ecart_minutes(actual: Optional[datetime], planned_time: Optional[dt_time]) -> Optional[int]:
    """
    Calcule l'écart en minutes entre l'heure réelle et l'heure planifiée.
    Positif = en retard, négatif = en avance.
    On compare uniquement heure/minute (même jour).
    """
    if actual is None or planned_time is None:
        return None
    if actual.tzinfo is None:
        actual = actual.replace(tzinfo=timezone.utc)
    # Reconstruire un datetime planifié sur la même date que l'événement réel
    planned_dt = datetime(
        actual.year, actual.month, actual.day,
        planned_time.hour, planned_time.minute,
        tzinfo=timezone.utc,
    )
    return round((actual - planned_dt).total_seconds() / 60)


# ── Schémas de réponse ────────────────────────────────────────────────────────

class ExecutionItem(BaseModel):
    """Une séance réellement effectuée pour ce créneau planifié."""
    seance_id:            str
    status:               str
    started_at:           Optional[str]  = None
    finished_at:          Optional[str]  = None
    duree_minutes:        Optional[int]  = None
    nb_eleves_presents:   Optional[int]  = None
    nb_eleves_total:      Optional[int]  = None
    soumis_offline:       bool           = False
    has_rapport:          bool           = False
    ecart_debut_minutes:  Optional[int]  = None  # + = en retard, - = en avance
    ecart_fin_minutes:    Optional[int]  = None


class SuiviPlanningItem(BaseModel):
    """Un créneau de planning avec toutes ses exécutions réelles."""
    segment_id:   str
    teacher_id:   Optional[str]  = None
    teacher_name: Optional[str]  = None
    school_name:  Optional[str]  = None
    jour:         int
    jour_label:   str
    heure_debut:  str            # "HH:MM"
    heure_fin:    str            # "HH:MM"
    duree_prevue: int            # minutes planifiées
    classe:       Optional[str]  = None
    matiere:      Optional[str]  = None
    executions:   list[ExecutionItem] = []
    # Métriques agrégées
    nb_executions:     int          = 0
    derniere_execution: Optional[str] = None  # ISO 8601


# ── Route ─────────────────────────────────────────────────────────────────────

@router.get("", response_model=list[SuiviPlanningItem])
async def list_suivi_plannings(
    db: DB,
    _: AdminUser,
    session_id: Optional[uuid.UUID] = Query(None, description="Filtrer par session"),
    teacher_id: Optional[uuid.UUID] = Query(None, description="Filtrer par enseignant"),
    jour:       Optional[int]       = Query(None, description="Filtrer par jour (0=Lundi … 6=Dimanche)"),
) -> list[SuiviPlanningItem]:
    """
    Retourne tous les créneaux de planning avec, pour chacun,
    la liste des séances réellement effectuées (started_at / finished_at).

    Permet à l'admin de comparer heure planifiée vs heure réelle,
    et de voir les données soumises hors-ligne une fois synchronisées.
    """
    from app.models.school import School

    # ── 1. Récupérer les segments de planning ─────────────────────────────────
    q_segs = select(
        PlanningSegment,
        User.name.label("teacher_name"),
        School.name.label("school_name"),
    ).outerjoin(User,   User.id   == PlanningSegment.teacher_id)\
     .outerjoin(School, School.id == PlanningSegment.school_id)\
     .order_by(PlanningSegment.jour.asc(), PlanningSegment.heure_debut.asc())

    if session_id:
        q_segs = q_segs.where(PlanningSegment.session_id == session_id)
    if teacher_id:
        q_segs = q_segs.where(PlanningSegment.teacher_id == teacher_id)
    if jour is not None:
        q_segs = q_segs.where(PlanningSegment.jour == jour)

    rows_segs = (await db.execute(q_segs)).all()
    if not rows_segs:
        return []

    seg_ids = [r.PlanningSegment.id for r in rows_segs]

    # ── 2. Récupérer toutes les séances liées à ces segments ──────────────────
    q_seances = (
        select(Seance)
        .where(Seance.planning_segment_id.in_(seg_ids))
        .order_by(Seance.started_at.asc().nulls_last())
    )
    if session_id:
        q_seances = q_seances.where(Seance.session_id == session_id)

    all_seances: list[Seance] = (await db.execute(q_seances)).scalars().all()

    # ── 3. Récupérer les rapports pour savoir si soumis offline ───────────────
    rapports_map: dict[uuid.UUID, RapportProf] = {}
    if all_seances:
        rapports = (await db.execute(
            select(RapportProf).where(
                RapportProf.seance_id.in_([s.id for s in all_seances])
            )
        )).scalars().all()
        rapports_map = {r.seance_id: r for r in rapports}

    # ── 4. Grouper les séances par planning_segment_id ────────────────────────
    seances_by_seg: dict[uuid.UUID, list[Seance]] = {sid: [] for sid in seg_ids}
    for seance in all_seances:
        if seance.planning_segment_id and seance.planning_segment_id in seances_by_seg:
            seances_by_seg[seance.planning_segment_id].append(seance)

    # ── 5. Construire la réponse ──────────────────────────────────────────────
    result: list[SuiviPlanningItem] = []

    for row in rows_segs:
        seg: PlanningSegment = row.PlanningSegment
        t_name: Optional[str] = row.teacher_name
        s_name: Optional[str] = row.school_name

        # Durée planifiée en minutes
        planned_start_min = seg.heure_debut.hour * 60 + seg.heure_debut.minute
        planned_end_min   = seg.heure_fin.hour   * 60 + seg.heure_fin.minute
        duree_prevue      = max(0, planned_end_min - planned_start_min)

        # Construire les exécutions
        executions: list[ExecutionItem] = []
        for seance in seances_by_seg.get(seg.id, []):
            rapport = rapports_map.get(seance.id)
            executions.append(ExecutionItem(
                seance_id=str(seance.id),
                status=seance.status.value,
                started_at=_iso(seance.started_at),
                finished_at=_iso(seance.finished_at),
                duree_minutes=seance.duree_minutes,
                nb_eleves_presents=seance.nb_eleves_presents,
                nb_eleves_total=seance.nb_eleves_total,
                soumis_offline=rapport.soumis_en_offline if rapport else False,
                has_rapport=rapport is not None,
                ecart_debut_minutes=_ecart_minutes(seance.started_at, seg.heure_debut),
                ecart_fin_minutes=_ecart_minutes(seance.finished_at, seg.heure_fin),
            ))

        derniere = max(
            (e.started_at for e in executions if e.started_at),
            default=None,
        )

        result.append(SuiviPlanningItem(
            segment_id=str(seg.id),
            teacher_id=str(seg.teacher_id) if seg.teacher_id else None,
            teacher_name=t_name,
            school_name=s_name,
            jour=seg.jour,
            jour_label=JOURS_FR[seg.jour] if 0 <= seg.jour <= 6 else str(seg.jour),
            heure_debut=_time_str(seg.heure_debut) or "",
            heure_fin=_time_str(seg.heure_fin)     or "",
            duree_prevue=duree_prevue,
            classe=seg.classe,
            matiere=seg.matiere,
            executions=executions,
            nb_executions=len(executions),
            derniere_execution=derniere,
        ))

    return result
