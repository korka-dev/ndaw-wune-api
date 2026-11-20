"""Service — configuration semaines/jours (par école/session) et progression
courante d'un enseignant (dérivée du dernier rapport journalier soumis)."""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.progression_config import (
    DEFAULT_NB_JOURS,
    DEFAULT_NB_SEMAINES,
    ProgressionConfig,
)
from app.models.rapport_journalier import RAPPORT_COMPTE, RapportJournalier


async def get_config_for(
    db: AsyncSession,
    school_id: Optional[uuid.UUID],
    session_id: Optional[uuid.UUID],
) -> tuple[int, int]:
    """Résout (nb_semaines, nb_jours) du plus précis au plus général :
    (école, session) → (école, toutes sessions) → (toutes écoles, session)
    → (toutes écoles, toutes sessions) → défaut (10, 3).

    La dernière étape est la configuration « repli global » (school_id et
    session_id à NULL) : c'est celle que l'admin crée par défaut, elle doit
    donc toujours être consultée avant de retomber sur les constantes."""
    candidates: list[tuple[Optional[uuid.UUID], Optional[uuid.UUID]]] = []
    if school_id is not None and session_id is not None:
        candidates.append((school_id, session_id))
    if school_id is not None:
        candidates.append((school_id, None))
    if session_id is not None:
        candidates.append((None, session_id))
    candidates.append((None, None))

    for s_id, sess_id in candidates:
        row = (
            await db.execute(
                select(ProgressionConfig).where(
                    ProgressionConfig.school_id == s_id,
                    ProgressionConfig.session_id == sess_id,
                )
            )
        ).scalar_one_or_none()
        if row is not None:
            return row.nb_semaines, row.nb_jours

    return DEFAULT_NB_SEMAINES, DEFAULT_NB_JOURS


async def get_current_progression(db: AsyncSession, teacher_id: uuid.UUID) -> Optional[dict]:
    """Dernier rapport journalier soumis par l'enseignant → {semaine, jour_cours, date_rapport}."""
    rapport = (
        await db.execute(
            select(RapportJournalier)
            .where(RapportJournalier.teacher_id == teacher_id, RAPPORT_COMPTE)
            .order_by(RapportJournalier.date_rapport.desc(), RapportJournalier.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if rapport is None:
        return None
    return {
        "semaine": rapport.semaine,
        "jour_cours": rapport.jour_cours,
        "date_rapport": rapport.date_rapport.isoformat(),
    }


async def get_latest_progression_by_teacher(
    db: AsyncSession, teacher_ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict]:
    """Version batch (pas de N+1) : {teacher_id: {semaine, jour_cours, date_rapport}}
    pour la dernière soumission de chaque enseignant."""
    if not teacher_ids:
        return {}
    rows = (
        await db.execute(
            select(RapportJournalier)
            .where(RapportJournalier.teacher_id.in_(teacher_ids), RAPPORT_COMPTE)
            .order_by(
                RapportJournalier.teacher_id,
                RapportJournalier.date_rapport.desc(),
                RapportJournalier.created_at.desc(),
            )
        )
    ).scalars().all()

    result: dict[uuid.UUID, dict] = {}
    for r in rows:
        if r.teacher_id in result:
            continue  # déjà la plus récente (rows triées par teacher_id, date desc)
        result[r.teacher_id] = {
            "semaine": r.semaine,
            "jour_cours": r.jour_cours,
            "date_rapport": r.date_rapport.isoformat(),
        }
    return result


async def active_session_for(db: AsyncSession, user) -> Optional["ProgramSession"]:
    """Session active de l'utilisateur (affectation), sinon la plus récente active
    — même résolution que /app/sync."""
    from app.models.session import ProgramSession, SessionStatus, TeacherSession

    active = (await db.execute(
        select(ProgramSession)
        .join(TeacherSession, TeacherSession.session_id == ProgramSession.id)
        .where(TeacherSession.teacher_id == user.id, ProgramSession.status == SessionStatus.active)
        .order_by(ProgramSession.date_debut.desc())
    )).scalars().first()
    if active is None:
        active = (await db.execute(
            select(ProgramSession)
            .where(ProgramSession.status == SessionStatus.active)
            .order_by(ProgramSession.date_debut.desc())
        )).scalars().first()
    return active


async def check_bornes_campagne(db: AsyncSession, user, semaine: int, jour: int) -> None:
    """422 par champ si semaine / jour dépassent la configuration de la campagne
    (rapport V2, APP-28 : règles paramétrées, pas de quotas copiés en dur)."""
    from fastapi import HTTPException

    active = await active_session_for(db, user)
    nb_semaines, nb_jours = await get_config_for(db, user.school_id, active.id if active else None)
    erreurs = []
    if semaine > nb_semaines:
        erreurs.append({"loc": ["body", "semaine"], "type": "value_error",
                        "msg": f"semaine {semaine} hors campagne (1 à {nb_semaines})."})
    if jour > nb_jours:
        erreurs.append({"loc": ["body", "jour_cours"], "type": "value_error",
                        "msg": f"jour de cours {jour} hors campagne (1 à {nb_jours})."})
    if erreurs:
        raise HTTPException(status_code=422, detail=erreurs)
