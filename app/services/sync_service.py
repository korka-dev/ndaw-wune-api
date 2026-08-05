"""
Service de synchronisation offline.
Construit le payload complet en un minimum de requêtes SQL.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.models.eleve import Eleve
from app.models.evaluation_eleve import EvaluationEleve
from app.models.planning import PlanningSegment
from app.models.rapport_difficulte import RapportDifficulte
from app.models.rapport_libelle import RapportLibelle
from app.models.rapport_question import RapportQuestion
from app.models.school import School
from app.models.session import ProgramSession, SessionStatus, TeacherSession
from app.models.user import User
from app.services.progression_service import get_config_for
from app.services.school_service import get_matching_school_ids
from app.schemas.sync import (
    SyncEleve,
    SyncPayload,
    SyncPlanningSegment,
    SyncProfile,
    SyncRapportDifficulte,
    SyncRapportQuestion,
    SyncSchool,
    SyncSession,
    SyncStats,
)


async def build_sync_payload(db: AsyncSession, user: User) -> SyncPayload:
    """
    Construit le SyncPayload en 3 requêtes max (profil déjà chargé).
    Requête 1 : école de l'enseignant
    Requête 2 : session active de l'enseignant (via JOIN)
    Requête 3 : planning de l'enseignant pour la session active
    """
    # ── Profil ────────────────────────────────────────────────────────────────
    profile = SyncProfile.model_validate(user)

    # ── École ─────────────────────────────────────────────────────────────────
    school_data: SyncSchool | None = None
    if user.school_id:
        school = (
            await db.execute(select(School).where(School.id == user.school_id))
        ).scalar_one_or_none()
        if school:
            school_data = SyncSchool.model_validate(school)

    # ── Session active ────────────────────────────────────────────────────────
    # 1) Session spécifique au professeur (via teacher_sessions)
    # 2) Fallback : session globale active (si le prof n'a pas encore été affecté)
    active_session: SyncSession | None = None
    planning_items: list[SyncPlanningSegment] = []

    active = (
        await db.execute(
            select(ProgramSession)
            .join(TeacherSession, TeacherSession.session_id == ProgramSession.id)
            .where(
                TeacherSession.teacher_id == user.id,
                ProgramSession.status     == SessionStatus.active,
            )
            .order_by(ProgramSession.date_debut.desc())
            .limit(1)
        )
    ).scalars().first()

    if active is None:
        # Fallback : la session globale active (la plus récente)
        active = (
            await db.execute(
                select(ProgramSession)
                .where(ProgramSession.status == SessionStatus.active)
                .order_by(ProgramSession.date_debut.desc())
            )
        ).scalars().first()

    if active:
        active_session = SyncSession.model_validate(active)

        # ── Planning ──────────────────────────────────────────────────────────
        # Inclut : segments assignés au prof OU segments partagés (teacher_id NULL)
        segments = (
            await db.execute(
                select(PlanningSegment)
                .where(
                    PlanningSegment.session_id == active.id,
                    or_(
                        PlanningSegment.teacher_id == user.id,
                        PlanningSegment.teacher_id.is_(None),
                    ),
                )
                .order_by(PlanningSegment.jour, PlanningSegment.heure_debut)
            )
        ).scalars().all()

        planning_items = [SyncPlanningSegment.model_validate(s) for s in segments]

    # ── Élèves liés à l'enseignant ────────────────────────────────────────────
    eleves_items: list[SyncEleve] = []
    if user.school_id and user.classes and user.school:
        # Comparaison normalisée (espaces/casse) pour éviter qu'un écart de
        # saisie entre la classe de l'enseignant et celle des élèves importés
        # (ex. "CM1 A" vs "cm1a") ne fasse disparaître les élèves de la sync.
        normalized_classes = [
            " ".join(c.strip().split()).lower() for c in user.classes if c and c.strip()
        ]
        # Les imports ont créé des fiches école dupliquées pour un même
        # établissement (« EE BATTAL » / « EFA BATTAL ») : un enseignant peut
        # être rattaché à l'une pendant que ses élèves sont répartis sur les
        # deux. On élargit donc la recherche à toutes les fiches de même nom
        # normalisé, comme pour le périmètre superviseur.
        school_ids = await get_matching_school_ids(db, user.school)
        rows = (
            await db.execute(
                select(Eleve)
                .where(
                    Eleve.school_id.in_(school_ids),
                    func.lower(func.regexp_replace(Eleve.classe, r"\s+", " ", "g")).in_(normalized_classes),
                    Eleve.statut == "actif",
                )
                .order_by(Eleve.classe, Eleve.nom, Eleve.prenom)
            )
        ).scalars().all()
        eleves_items = [SyncEleve.model_validate(e) for e in rows]

    # ── Questions complémentaires du rapport journalier (configurées par l'admin) ──
    # Ne renvoie que les questions destinées au tuteur (ou à "tous").
    questions_rows = (
        await db.execute(
            select(RapportQuestion)
            .where(
                RapportQuestion.active.is_(True),
                RapportQuestion.cible.in_(["tuteur", "tous"]),
            )
            .order_by(RapportQuestion.ordre, RapportQuestion.created_at)
        )
    ).scalars().all()
    rapport_questions_items = [SyncRapportQuestion.model_validate(q) for q in questions_rows]

    # ── Liste des difficultés (configurée par l'admin) ───────────────────────
    difficultes_rows = (
        await db.execute(
            select(RapportDifficulte)
            .where(RapportDifficulte.active.is_(True))
            .order_by(RapportDifficulte.ordre, RapportDifficulte.created_at)
        )
    ).scalars().all()
    rapport_difficultes_items = [SyncRapportDifficulte.model_validate(d) for d in difficultes_rows]

    # ── Libellés éditables des champs fixes du rapport (configurés par l'admin) ──
    libelles_rows = (
        await db.execute(
            select(RapportLibelle).where(RapportLibelle.cible == "tuteur")
        )
    ).scalars().all()
    rapport_libelles = {l.cle: l.texte for l in libelles_rows}

    # ── Nombre de semaines/jours du programme (configurable école/session) ──
    nb_semaines, nb_jours = await get_config_for(
        db, user.school_id, active.id if active else None
    )

    # ── Stats du profil : élèves, tests (évaluations sur ses élèves), fiches ──
    nb_tests = 0
    if eleves_items:
        nb_tests = (
            await db.execute(
                select(func.count(EvaluationEleve.id)).where(
                    EvaluationEleve.eleve_id.in_([e.id for e in eleves_items])
                )
            )
        ).scalar_one()

    langue_ecole = school_data.langue if school_data else None
    nb_fiches = (
        await db.execute(
            select(func.count(Document.id)).where(
                or_(Document.langue.is_(None), Document.langue == langue_ecole)
            )
        )
    ).scalar_one()

    stats = SyncStats(
        nb_eleves=len(eleves_items),
        nb_tests=nb_tests,
        nb_fiches=nb_fiches,
    )

    return SyncPayload(
        synced_at=datetime.now(timezone.utc),
        profile=profile,
        school=school_data,
        active_session=active_session,
        planning=planning_items,
        eleves=eleves_items,
        rapport_questions=rapport_questions_items,
        rapport_difficultes=rapport_difficultes_items,
        rapport_libelles=rapport_libelles,
        nb_semaines=nb_semaines,
        nb_jours=nb_jours,
        stats=stats,
    )
