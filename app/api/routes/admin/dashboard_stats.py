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
from app.models.school_classe import SchoolClasse
from app.models.seance import RapportProf, Seance
from app.models.session import ProgramSession, SessionStatus
from app.models.planning import PlanningSegment
from app.models.eleve import Eleve
from app.models.eleve_remplacement import EleveRemplacement
from app.models.evaluation_eleve import EvaluationEleve
from app.models.evaluation_competence import EvaluationCompetence
from app.models.evaluation_doc import EvaluationDoc
from app.models.rapport_journalier import RapportJournalier
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


class SuperviseurEvalActivity(BaseModel):
    name:                 str
    total:                int
    evaluations_semaine:  int
    jours_depuis_dernier: Optional[int] = None   # None = n'a jamais évalué


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

    # ── Autres pages (chiffres + répartitions) ─────────────────────────────────
    classes_total:              int
    evaluations_total:          int
    rapports_journaliers_total: int
    remplacements_total:        int
    competences_total:          int
    evaluation_docs_total:      int

    evaluations_by_resultat:    list[ChartPoint]
    eleves_by_groupe_lecture:   list[ChartPoint]
    eleves_by_groupe_maths:     list[ChartPoint]
    eleves_by_statut_selection: list[ChartPoint]

    # ── Suivi évaluation (pour les responsables du suivi terrain) ─────────────
    evaluations_students_covered:  int            # élèves évalués au moins 1 fois
    evaluations_coverage_pct:      int             # % élèves évalués / total élèves actifs
    superviseurs_actifs_semaine:   int            # ont évalué depuis lundi
    evaluations_by_week:           list[ChartPoint]   # 8 dernières semaines
    evaluations_a_aider_by_competence: list[ChartPoint]  # compétences les + en difficulté (top 6)
    superviseurs_eval_activity:    list[SuperviseurEvalActivity]  # les moins actifs d'abord


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

    # ── Autres pages : chiffres simples (COUNT) ───────────────────────────────

    classes_total = (await db.execute(
        select(func.count()).select_from(SchoolClasse)
    )).scalar_one()

    evaluations_total = (await db.execute(
        select(func.count()).select_from(EvaluationEleve)
    )).scalar_one()

    rapports_journaliers_total = (await db.execute(
        select(func.count()).select_from(RapportJournalier)
    )).scalar_one()

    remplacements_total = (await db.execute(
        select(func.count()).select_from(EleveRemplacement)
    )).scalar_one()

    competences_total = (await db.execute(
        select(func.count()).select_from(EvaluationCompetence).where(EvaluationCompetence.active.is_(True))
    )).scalar_one()

    evaluation_docs_total = (await db.execute(
        select(func.count()).select_from(EvaluationDoc).where(EvaluationDoc.is_active.is_(True))
    )).scalar_one()

    # ── Évaluations par résultat (acquis / en_cours / a_aider) ────────────────

    resultat_rows = (await db.execute(
        select(EvaluationEleve.resultat, func.count())
        .group_by(EvaluationEleve.resultat)
    )).all()
    evaluations_by_resultat = [ChartPoint(label=r[0], value=r[1]) for r in resultat_rows]

    # ── Élèves par groupe lecture / maths / statut de sélection ───────────────

    lecture_rows = (await db.execute(
        select(func.coalesce(Eleve.groupe_lecture, "—"), func.count())
        .where(Eleve.statut == "actif")
        .group_by(Eleve.groupe_lecture)
    )).all()
    eleves_by_groupe_lecture = [ChartPoint(label=r[0], value=r[1]) for r in lecture_rows]

    maths_rows = (await db.execute(
        select(func.coalesce(Eleve.groupe_maths, "—"), func.count())
        .where(Eleve.statut == "actif")
        .group_by(Eleve.groupe_maths)
    )).all()
    eleves_by_groupe_maths = [ChartPoint(label=r[0], value=r[1]) for r in maths_rows]

    statut_selection_rows = (await db.execute(
        select(func.coalesce(Eleve.statut_selection, "—"), func.count())
        .where(Eleve.statut == "actif")
        .group_by(Eleve.statut_selection)
    )).all()
    eleves_by_statut_selection = [ChartPoint(label=r[0], value=r[1]) for r in statut_selection_rows]

    # ── Suivi évaluation ───────────────────────────────────────────────────────

    evaluations_students_covered = (await db.execute(
        select(func.count(func.distinct(EvaluationEleve.eleve_id)))
    )).scalar_one()

    evaluations_coverage_pct = (
        round(evaluations_students_covered / students_count * 100) if students_count else 0
    )

    week_start = today - timedelta(days=today.weekday())

    superviseurs_actifs_semaine = (await db.execute(
        select(func.count(func.distinct(EvaluationEleve.superviseur_id)))
        .where(EvaluationEleve.date_eval >= week_start)
    )).scalar_one()

    # Tendance : évaluations par semaine (8 dernières semaines, lundi → dimanche)
    eight_weeks_ago = week_start - timedelta(weeks=7)
    eval_week_rows = (await db.execute(
        select(EvaluationEleve.date_eval, func.count())
        .where(EvaluationEleve.date_eval >= eight_weeks_ago)
        .group_by(EvaluationEleve.date_eval)
    )).all()
    week_bucket: dict[date, int] = {}
    for d, cnt in eval_week_rows:
        wk_monday = d - timedelta(days=d.weekday())
        week_bucket[wk_monday] = week_bucket.get(wk_monday, 0) + cnt

    evaluations_by_week: list[ChartPoint] = []
    for i in range(8):
        wk = week_start - timedelta(weeks=7 - i)
        evaluations_by_week.append(ChartPoint(
            label=wk.strftime("%d/%m"),
            value=week_bucket.get(wk, 0),
        ))

    # Compétences les plus en difficulté (nb de résultats "a_aider", top 6)
    competence_labels = {c.code: c.label for c in (await db.execute(select(EvaluationCompetence))).scalars().all()}
    a_aider_rows = (await db.execute(
        select(EvaluationEleve.competence, func.count())
        .where(EvaluationEleve.resultat == "a_aider")
        .group_by(EvaluationEleve.competence)
        .order_by(func.count().desc())
        .limit(6)
    )).all()
    evaluations_a_aider_by_competence = [
        ChartPoint(label=competence_labels.get(comp, comp), value=cnt) for comp, cnt in a_aider_rows
    ]

    # Activité des superviseurs — les moins actifs en premier (repérer qui est en retard)
    sup_totals = (await db.execute(
        select(
            EvaluationEleve.superviseur_id,
            func.count().label("total"),
            func.max(EvaluationEleve.date_eval).label("last_date"),
        ).group_by(EvaluationEleve.superviseur_id)
    )).all()
    sup_totals_map = {r.superviseur_id: (r.total, r.last_date) for r in sup_totals}

    sup_week_rows = (await db.execute(
        select(EvaluationEleve.superviseur_id, func.count())
        .where(EvaluationEleve.date_eval >= week_start)
        .group_by(EvaluationEleve.superviseur_id)
    )).all()
    sup_week_map = {sid: cnt for sid, cnt in sup_week_rows}

    all_superviseurs = (await db.execute(
        select(User).where(User.role == UserRole.superviseur, User.status == UserStatus.actif)
    )).scalars().all()

    activity_rows = []
    for sup in all_superviseurs:
        total, last_date = sup_totals_map.get(sup.id, (0, None))
        activity_rows.append(SuperviseurEvalActivity(
            name=sup.name,
            total=total,
            evaluations_semaine=sup_week_map.get(sup.id, 0),
            jours_depuis_dernier=(today - last_date).days if last_date else None,
        ))
    activity_rows.sort(key=lambda r: r.evaluations_semaine)
    superviseurs_eval_activity = activity_rows[:8]

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
        classes_total=classes_total,
        evaluations_total=evaluations_total,
        rapports_journaliers_total=rapports_journaliers_total,
        remplacements_total=remplacements_total,
        competences_total=competences_total,
        evaluation_docs_total=evaluation_docs_total,
        evaluations_by_resultat=evaluations_by_resultat,
        eleves_by_groupe_lecture=eleves_by_groupe_lecture,
        eleves_by_groupe_maths=eleves_by_groupe_maths,
        eleves_by_statut_selection=eleves_by_statut_selection,
        evaluations_students_covered=evaluations_students_covered,
        evaluations_coverage_pct=evaluations_coverage_pct,
        superviseurs_actifs_semaine=superviseurs_actifs_semaine,
        evaluations_by_week=evaluations_by_week,
        evaluations_a_aider_by_competence=evaluations_a_aider_by_competence,
        superviseurs_eval_activity=superviseurs_eval_activity,
    )
