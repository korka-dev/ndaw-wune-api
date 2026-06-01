"""
Couche service pour les séances (timer) et les rapports enseignants.
"""
from __future__ import annotations

import logging
import uuid
from typing import Sequence

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.seance import RapportProf, Seance, SeanceStatus
from app.schemas.seance import SeanceFinish, SeancePauseBody, SeanceResumeBody, SeanceStart, SeanceMissedReport, RapportCreate

logger = logging.getLogger(__name__)


# ── Séances ────────────────────────────────────────────────────────────────────

async def report_missed_seance(
    db: AsyncSession,
    teacher_id: uuid.UUID,
    body: SeanceMissedReport,
) -> Seance:
    """
    Crée une séance avec status=manquee pour traçabilité.
    Idempotent : si une séance manquée existe déjà pour ce segment ce jour-là, retourne l'existante.
    """
    logger.info(
        "report_missed_seance: teacher=%s segment=%s classe=%s",
        teacher_id, body.planning_segment_id, body.classe,
    )
    # Idempotence : un seul enregistrement manqué par segment par jour
    if body.planning_segment_id:
        from sqlalchemy import func as sqlfunc
        existing = (await db.execute(
            select(Seance).where(
                Seance.teacher_id           == teacher_id,
                Seance.planning_segment_id  == body.planning_segment_id,
                Seance.status               == SeanceStatus.manquee,
                sqlfunc.date(Seance.date_seance) == sqlfunc.date(body.date_seance),
            )
        )).scalar_one_or_none()
        if existing:
            return existing

    seance = Seance(
        teacher_id          = teacher_id,
        session_id          = body.session_id,
        planning_segment_id = body.planning_segment_id,
        classe              = body.classe,
        matiere             = body.matiere,
        date_seance         = body.date_seance,
        status              = SeanceStatus.manquee,
        started_at          = None,
        finished_at         = None,
        duree_minutes       = 0,
    )
    db.add(seance)
    await db.flush()
    await db.refresh(seance)
    return seance


async def get_active_seance(db: AsyncSession, teacher_id: uuid.UUID) -> Seance | None:
    result = await db.execute(
        select(Seance).where(
            Seance.teacher_id == teacher_id,
            Seance.status     == SeanceStatus.en_cours,
        )
    )
    return result.scalar_one_or_none()


async def start_seance(db: AsyncSession, teacher_id: uuid.UUID, body: SeanceStart) -> Seance:
    """Démarre le timer. Lève 409 si une séance est déjà en cours."""
    logger.info(
        "start_seance: teacher=%s session=%s classe=%s planning_segment=%s",
        teacher_id, body.session_id, body.classe, body.planning_segment_id,
    )
    try:
        existing = await get_active_seance(db, teacher_id)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Une séance est déjà en cours. Terminez-la avant d'en démarrer une nouvelle.",
            )

        seance = Seance(teacher_id=teacher_id, **body.model_dump())
        db.add(seance)
        try:
            await db.flush()
        except SQLAlchemyError as exc:
            await db.rollback()
            orig = getattr(exc, "orig", exc)
            logger.error("DB Error lors de start_seance teacher=%s : %s", teacher_id, orig)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Impossible de créer la séance : {orig}",
            )
        await db.refresh(seance)
        return seance

    except HTTPException:
        raise  # laisser passer les HTTPException normales (409, 400...)
    except Exception as exc:
        # Attrape toute exception inattendue et l'expose en 400 (pas 500)
        logger.error(
            "Erreur inattendue dans start_seance teacher=%s : %r",
            teacher_id, exc, exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Erreur inattendue lors du démarrage : {type(exc).__name__}: {exc}",
        )


async def _get_seance_owned(
    db: AsyncSession,
    seance_id: uuid.UUID,
    teacher_id: uuid.UUID,
) -> Seance:
    """Récupère une séance en vérifiant l'ownership. Lève 404 si introuvable."""
    result = await db.execute(
        select(Seance).where(
            Seance.id         == seance_id,
            Seance.teacher_id == teacher_id,
        )
    )
    seance = result.scalar_one_or_none()
    if seance is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Séance introuvable.")
    return seance


async def record_pause(
    db: AsyncSession,
    seance_id: uuid.UUID,
    teacher_id: uuid.UUID,
    body: SeancePauseBody,
) -> Seance:
    """Enregistre un événement de mise en pause dans la séance."""
    seance = await _get_seance_owned(db, seance_id, teacher_id)
    if seance.status != SeanceStatus.en_cours:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cette séance n'est plus en cours.")

    pauses = list(seance.pauses or [])
    pauses.append({"paused_at": body.paused_at.isoformat(), "resumed_at": None})
    seance.pauses = pauses

    await db.flush()
    await db.refresh(seance)
    return seance


async def record_resume(
    db: AsyncSession,
    seance_id: uuid.UUID,
    teacher_id: uuid.UUID,
    body: SeanceResumeBody,
) -> Seance:
    """Ferme le dernier événement de pause (resumed_at) et recalcule total_paused_minutes."""
    seance = await _get_seance_owned(db, seance_id, teacher_id)
    if seance.status != SeanceStatus.en_cours:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cette séance n'est plus en cours.")

    pauses = list(seance.pauses or [])

    # Fermer la dernière pause ouverte
    for p in reversed(pauses):
        if p.get("resumed_at") is None:
            p["resumed_at"] = body.resumed_at.isoformat()
            break

    # Recalculer le total des pauses (en minutes)
    from datetime import datetime as dt
    total_ms = 0
    for p in pauses:
        if p.get("paused_at") and p.get("resumed_at"):
            try:
                pa = dt.fromisoformat(p["paused_at"])
                ra = dt.fromisoformat(p["resumed_at"])
                total_ms += max(0, int((ra - pa).total_seconds() * 1000))
            except Exception:
                pass
    seance.pauses               = pauses
    seance.total_paused_minutes = max(0, round(total_ms / 60000))

    await db.flush()
    await db.refresh(seance)
    return seance


async def finish_seance(
    db: AsyncSession,
    seance_id: uuid.UUID,
    teacher_id: uuid.UUID,
    body: SeanceFinish,
) -> Seance:
    """Clôture le timer. Fusionne les pauses offline si fournies."""
    logger.info(
        "finish_seance: seance_id=%s teacher=%s finished_at=%s duree=%s pauses=%d",
        seance_id, teacher_id, body.finished_at, body.duree_minutes, len(body.pauses),
    )
    try:
        seance = await _get_seance_owned(db, seance_id, teacher_id)
        if seance.status != SeanceStatus.en_cours:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cette séance n'est plus en cours.",
            )

        seance.finished_at        = body.finished_at
        seance.duree_minutes      = body.duree_minutes
        seance.nb_eleves_presents = body.nb_eleves_presents
        seance.status             = SeanceStatus.terminee

        # Fusionner les pauses offline si le serveur n'en a pas encore
        if body.pauses and not seance.pauses:
            from sqlalchemy.orm.attributes import flag_modified
            seance.pauses = [p.model_dump() for p in body.pauses]
            flag_modified(seance, "pauses")
        if body.total_paused_minutes is not None and seance.total_paused_minutes is None:
            seance.total_paused_minutes = body.total_paused_minutes

        try:
            await db.flush()
        except SQLAlchemyError as exc:
            await db.rollback()
            orig = getattr(exc, "orig", exc)
            logger.error("DB Error lors de finish_seance seance_id=%s : %s", seance_id, orig)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Impossible de clôturer la séance : {orig}",
            )
        await db.refresh(seance)
        return seance

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "Erreur inattendue dans finish_seance seance_id=%s : %r",
            seance_id, exc, exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Erreur inattendue lors de la clôture : {type(exc).__name__}: {exc}",
        )


async def list_teacher_seances(
    db: AsyncSession,
    teacher_id: uuid.UUID,
    skip: int = 0,
    limit: int = 50,
) -> tuple[int, Sequence[Seance]]:
    base = (
        select(Seance)
        .where(Seance.teacher_id == teacher_id)
        .order_by(Seance.date_seance.desc())
    )
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    items = (await db.execute(base.offset(skip).limit(limit))).scalars().all()
    return total, items


# ── Rapports ───────────────────────────────────────────────────────────────────

async def submit_rapport(
    db: AsyncSession,
    teacher_id: uuid.UUID,
    body: RapportCreate,
) -> RapportProf:
    logger.info("submit_rapport: teacher=%s seance_id=%s", teacher_id, body.seance_id)
    try:
        # Vérifier l'existence et l'ownership de la séance
        result = await db.execute(select(Seance).where(Seance.id == body.seance_id))
        seance = result.scalar_one_or_none()
        if seance is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Séance introuvable.")
        if seance.teacher_id != teacher_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cette séance ne vous appartient pas.")

        # Un seul rapport par séance (contrainte DB + vérification applicative)
        existing = (
            await db.execute(select(RapportProf).where(RapportProf.seance_id == body.seance_id))
        ).scalar_one_or_none()
        if existing:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Un rapport existe déjà pour cette séance.")

        rapport = RapportProf(teacher_id=teacher_id, **body.model_dump())
        db.add(rapport)
        try:
            await db.flush()
        except SQLAlchemyError as exc:
            await db.rollback()
            orig = getattr(exc, "orig", exc)
            logger.error("DB Error lors de submit_rapport teacher=%s seance_id=%s : %s", teacher_id, body.seance_id, orig)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Impossible de soumettre le rapport : {orig}",
            )
        await db.refresh(rapport)
        return rapport

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "Erreur inattendue dans submit_rapport teacher=%s seance_id=%s : %r",
            teacher_id, body.seance_id, exc, exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Erreur inattendue lors de la soumission du rapport : {type(exc).__name__}: {exc}",
        )


async def list_teacher_rapports(
    db: AsyncSession,
    teacher_id: uuid.UUID,
    skip: int = 0,
    limit: int = 50,
) -> tuple[int, Sequence[RapportProf]]:
    base = (
        select(RapportProf)
        .where(RapportProf.teacher_id == teacher_id)
        .order_by(RapportProf.created_at.desc())
    )
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    items = (await db.execute(base.offset(skip).limit(limit))).scalars().all()
    return total, items


async def list_admin_rapports(
    db: AsyncSession,
    session_id: uuid.UUID | None,
    teacher_id: uuid.UUID | None,
    skip: int = 0,
    limit: int = 50,
) -> tuple[int, Sequence[RapportProf]]:
    base = select(RapportProf).order_by(RapportProf.created_at.desc())
    if teacher_id:
        base = base.where(RapportProf.teacher_id == teacher_id)
    if session_id:
        base = base.join(Seance, Seance.id == RapportProf.seance_id).where(Seance.session_id == session_id)

    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    items = (await db.execute(base.offset(skip).limit(limit))).scalars().all()
    return total, items
