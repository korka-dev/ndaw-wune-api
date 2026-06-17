"""Endpoint Admin — Statistiques agrégées pour le dashboard.

Route : GET /admin/dashboard/stats

Retourne toutes les métriques du dashboard en un seul appel,
via des agrégations SQL efficaces (pas de chargement de données brutes).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select, case, extract

from app.core.deps import AdminUser, DB
from app.models.school import School
from app.models.seance import RapportProf, Seance
from app.models.session import ProgramSession, SessionStatus
from app.models.planning import PlanningSegment
from app.models.eleve import Eleve
from app.models.user import User, UserRole, UserStatus

router = APIRouter(prefix="/dashboard", tags=["Admin — Dashboard"])

MONTHS_FR = ["Jan", "Fév", "Mar", "Avr", "Mai", "Jun",
             "Jul", "Aoû", "Sep", "Oct", "Nov", "Déc"]

JOURS_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]


# ── Schémas de réponse ────────────────────────────────────────────────────────

class ActiveSession(BaseModel):
    name: str
    date_debut: str
    date_fin: str


class ChartPoint(BaseModel):
    label: str
    value: int


class DayPoint(BaseModel):
    label: str
    value: int   # minutes totales


class DashboardStats(BaseModel):
    # Entités
    schools:        int
    school_regions: int
    teachers:       int
    superviseurs:   int
    sessions_total: int
    students:       int
    active_session: Optional[ActiveSession]

    # Rapports & séances
    rapports_total:   int
    seances_by_month: list[ChartPoint]   # 6 derniers mois

    # Charts
    ecoles_by_region:    list[ChartPoint]
    teachers_by_school:  list[ChartPoint]  # top 5
    planning_by_day:     list[DayPoint]


# ── Route ─────────────────────────────────────────────────────────────────────

@router.get("/stats", response_model=DashboardStats)
async def dashboard_stats(_: AdminUser, db: DB) -> DashboardStats:
    """
    Agrège toutes les métriques du dashboard en un seul appel.
    Chaque métrique = 1 requête SQL COUNT/GROUP BY — aucun transfert de données brutes.
    """

    # ── Comptes de base (requêtes COUNT pures) ────────────────────────────────

    schools_count = (await db.execute(
        select(func.count()).select_from(School)
    )).scalar_one()

    school_regions_count = (await db.execute(
        select(func.count(func.distinct(School.region))).where(School.region.isnot(None))
    )).scalar_one()

    teachers_count = (await db.execute(
        select(func.count()).select_from(User).where(
            User.role == UserRole.enseignant,
            User.status == UserStatus.actif,
        )
    )).scalar_one()

    superviseurs_count = (await db.execute(
        select(func.count()).select_from(User).where(
            User.role == UserRole.superviseur,
            User.status == UserStatus.actif,
        )
    )).scalar_one()

    sessions_count = (await db.execute(
        select(func.count()).select_from(ProgramSession)
    )).scalar_one()

    students_count = (await db.execute(
        select(func.count()).select_from(Eleve).where(Eleve.statut == "actif")
    )).scalar_one()

    rapports_count = (await db.execute(
        select(func.count()).select_from(RapportProf)
    )).scalar_one()

    # ── Session active ────────────────────────────────────────────────────────

    active_session_row = (await db.execute(
        select(ProgramSession)
        .where(ProgramSession.status == SessionStatus.active)
        .limit(1)
    )).scalar_one_or_none()

    active_session = ActiveSession(
        name=active_session_row.name,
        date_debut=active_session_row.date_debut.isoformat(),
        date_fin=active_session_row.date_fin.isoformat(),
    ) if active_session_row else None

    # ── Séances par mois (6 derniers mois) ───────────────────────────────────
    # Agrégation SQL GROUP BY (year, month) — aucun chargement de données brutes

    today = date.today()
    six_months_ago = date(today.year, today.month, 1) - timedelta(days=150)

    monthly_rows = (await db.execute(
        select(
            extract("year",  RapportProf.created_at).label("yr"),
            extract("month", RapportProf.created_at).label("mo"),
            func.count().label("cnt"),
        )
        .where(RapportProf.created_at >= six_months_ago)
        .group_by("yr", "mo")
    )).all()

    monthly_map = {(int(r.yr), int(r.mo)): r.cnt for r in monthly_rows}

    seances_by_month: list[ChartPoint] = []
    for i in range(6):
        # date du premier jour du mois (5 mois en arrière → mois courant)
        mo_date = date(today.year, today.month, 1)
        for _ in range(5 - i):
            mo_date = (mo_date - timedelta(days=1)).replace(day=1)
        label = f"{MONTHS_FR[mo_date.month - 1]} {str(mo_date.year)[2:]}"
        seances_by_month.append(ChartPoint(
            label=label,
            value=monthly_map.get((mo_date.year, mo_date.month), 0),
        ))

    # ── Écoles par région ─────────────────────────────────────────────────────

    region_rows = (await db.execute(
        select(
            func.coalesce(School.region, "—").label("region"),
            func.count().label("cnt"),
        )
        .group_by(School.region)
        .order_by(func.count().desc())
    )).all()

    ecoles_by_region = [ChartPoint(label=r.region, value=r.cnt) for r in region_rows]

    # ── Enseignants par école (top 5) ─────────────────────────────────────────

    school_rows = (await db.execute(
        select(
            School.name.label("school_name"),
            func.count(User.id).label("cnt"),
        )
        .join(User, User.school_id == School.id)
        .where(User.role == UserRole.enseignant, User.status == UserStatus.actif)
        .group_by(School.id, School.name)
        .order_by(func.count(User.id).desc())
        .limit(5)
    )).all()

    teachers_by_school = [ChartPoint(label=r.school_name, value=r.cnt) for r in school_rows]

    # ── Planning par jour (durée totale en minutes) ───────────────────────────

    seg_rows = (await db.execute(
        select(
            PlanningSegment.jour.label("jour"),
            func.sum(
                (func.extract("hour",   PlanningSegment.heure_fin)   * 60 +
                 func.extract("minute", PlanningSegment.heure_fin))
                -
                (func.extract("hour",   PlanningSegment.heure_debut) * 60 +
                 func.extract("minute", PlanningSegment.heure_debut))
            ).label("total_min"),
        )
        .group_by(PlanningSegment.jour)
        .order_by(PlanningSegment.jour)
    )).all()

    planning_by_day = [
        DayPoint(label=JOURS_FR[r.jour], value=int(r.total_min or 0))
        for r in seg_rows
        if 0 <= r.jour < 7 and (r.total_min or 0) > 0
    ]

    return DashboardStats(
        schools=schools_count,
        school_regions=school_regions_count,
        teachers=teachers_count,
        superviseurs=superviseurs_count,
        sessions_total=sessions_count,
        students=students_count,
        active_session=active_session,
        rapports_total=rapports_count,
        seances_by_month=seances_by_month,
        ecoles_by_region=ecoles_by_region,
        teachers_by_school=teachers_by_school,
        planning_by_day=planning_by_day,
    )
