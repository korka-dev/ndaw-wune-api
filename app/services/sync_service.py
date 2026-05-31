"""
Service de synchronisation offline.
Construit le payload complet en un minimum de requêtes SQL.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.eleve import Eleve
from app.models.planning import PlanningSegment
from app.models.school import School
from app.models.session import ProgramSession, SessionStatus, TeacherSession
from app.models.user import User
from app.schemas.sync import (
    SyncEleve,
    SyncPayload,
    SyncPlanningSegment,
    SyncProfile,
    SyncSchool,
    SyncSession,
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
    if user.school_id and user.classes:
        rows = (
            await db.execute(
                select(Eleve)
                .where(
                    Eleve.school_id == user.school_id,
                    Eleve.classe.in_(user.classes),
                    Eleve.statut == "actif",
                )
                .order_by(Eleve.classe, Eleve.nom, Eleve.prenom)
            )
        ).scalars().all()
        eleves_items = [SyncEleve.model_validate(e) for e in rows]

    return SyncPayload(
        synced_at=datetime.now(timezone.utc),
        profile=profile,
        school=school_data,
        active_session=active_session,
        planning=planning_items,
        eleves=eleves_items,
    )
