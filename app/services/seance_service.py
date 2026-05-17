"""
Couche service pour les séances (timer) et les rapports enseignants.
"""
from __future__ import annotations

import uuid
from typing import Sequence

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.seance import RapportProf, Seance, SeanceStatus
from app.schemas.seance import SeanceFinish, SeanceStart, RapportCreate


# ── Séances ────────────────────────────────────────────────────────────────────

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
    existing = await get_active_seance(db, teacher_id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Une séance est déjà en cours. Terminez-la avant d'en démarrer une nouvelle.",
        )

    seance = Seance(teacher_id=teacher_id, **body.model_dump())
    db.add(seance)
    await db.flush()
    await db.refresh(seance)
    return seance


async def finish_seance(
    db: AsyncSession,
    seance_id: uuid.UUID,
    teacher_id: uuid.UUID,
    body: SeanceFinish,
) -> Seance:
    """Clôture le timer. Vérifie l'ownership et le statut."""
    result = await db.execute(
        select(Seance).where(
            Seance.id         == seance_id,
            Seance.teacher_id == teacher_id,
        )
    )
    seance = result.scalar_one_or_none()
    if seance is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Séance introuvable.")
    if seance.status != SeanceStatus.en_cours:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cette séance n'est plus en cours.")

    seance.finished_at        = body.finished_at
    seance.duree_minutes      = body.duree_minutes
    seance.nb_eleves_presents = body.nb_eleves_presents
    seance.status             = SeanceStatus.terminee

    await db.flush()
    await db.refresh(seance)
    return seance


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
    await db.flush()
    await db.refresh(rapport)
    return rapport


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
