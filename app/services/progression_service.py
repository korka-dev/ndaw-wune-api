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
from app.models.rapport_journalier import RapportJournalier


async def get_config_for(
    db: AsyncSession,
    school_id: Optional[uuid.UUID],
    session_id: Optional[uuid.UUID],
) -> tuple[int, int]:
    """Résout (nb_semaines, nb_jours) avec repli :
    (école, session) exact → (école, global) → (global, session) → défaut (10, 3)."""
    candidates: list[tuple[Optional[uuid.UUID], Optional[uuid.UUID]]] = []
    if school_id is not None and session_id is not None:
        candidates.append((school_id, session_id))
    if school_id is not None:
        candidates.append((school_id, None))
    if session_id is not None:
        candidates.append((None, session_id))

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
            .where(RapportJournalier.teacher_id == teacher_id)
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
            .where(RapportJournalier.teacher_id.in_(teacher_ids))
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
