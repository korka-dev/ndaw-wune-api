"""
Couche service pour la gestion des sessions de programme.
"""
from __future__ import annotations

import uuid
from typing import Sequence

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.session import ProgramSession, SessionStatus, TeacherSession
from app.models.user import User
from app.schemas.session import SessionCreate, SessionUpdate


async def get_by_id(db: AsyncSession, session_id: uuid.UUID) -> ProgramSession:
    result = await db.execute(select(ProgramSession).where(ProgramSession.id == session_id))
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session introuvable.")
    return session


async def list_sessions(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 50,
) -> tuple[int, Sequence[ProgramSession]]:
    base = select(ProgramSession).order_by(ProgramSession.date_debut.desc())
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    items = (await db.execute(base.offset(skip).limit(limit))).scalars().all()
    return total, items


async def create_session(db: AsyncSession, body: SessionCreate) -> ProgramSession:
    session = ProgramSession(**body.model_dump())
    db.add(session)
    await db.flush()
    await db.refresh(session)
    return session


async def update_session(db: AsyncSession, session_id: uuid.UUID, body: SessionUpdate) -> ProgramSession:
    session = await get_by_id(db, session_id)
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(session, field, value)
    await db.flush()
    await db.refresh(session)
    return session


async def delete_session(db: AsyncSession, session_id: uuid.UUID) -> None:
    session = await get_by_id(db, session_id)
    await db.delete(session)


async def activate_session(db: AsyncSession, session_id: uuid.UUID) -> ProgramSession:
    """
    Active une session et désactive toutes les autres en DEUX requêtes SQL,
    pas en chargeant toutes les sessions en Python.
    """
    # Vérifier que la session cible existe
    session = await get_by_id(db, session_id)

    # Désactiver toutes via UPDATE bulk (O(1) en SQL, pas de boucle Python)
    await db.execute(
        update(ProgramSession).values(status=SessionStatus.inactive)
    )
    # Activer la cible
    await db.execute(
        update(ProgramSession)
        .where(ProgramSession.id == session_id)
        .values(status=SessionStatus.active)
    )
    await db.flush()
    await db.refresh(session)
    return session


# ── Gestion des assignations enseignants ──────────────────────────────────────

async def assign_teachers(
    db: AsyncSession,
    session_id: uuid.UUID,
    teacher_ids: list[uuid.UUID],
) -> None:
    """
    Remplace intégralement les assignations de la session.
    Utilise DELETE + INSERT bulk plutôt que de chercher les diffs.
    """
    await get_by_id(db, session_id)  # 404 si session inconnue

    # Vérifier que tous les enseignants existent
    if teacher_ids:
        result = await db.execute(select(User.id).where(User.id.in_(teacher_ids)))
        found_ids = {row.id for row in result.all()}
        missing = set(teacher_ids) - found_ids
        if missing:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Enseignants introuvables : {[str(i) for i in missing]}",
            )

    # Supprimer les anciennes assignations
    await db.execute(delete(TeacherSession).where(TeacherSession.session_id == session_id))

    # Insérer les nouvelles
    for tid in teacher_ids:
        db.add(TeacherSession(teacher_id=tid, session_id=session_id))

    await db.flush()


async def list_session_teachers(
    db: AsyncSession,
    session_id: uuid.UUID,
) -> list[dict]:
    await get_by_id(db, session_id)

    result = await db.execute(
        select(TeacherSession, User.name)
        .join(User, User.id == TeacherSession.teacher_id)
        .where(TeacherSession.session_id == session_id)
        .order_by(User.name)
    )
    return [
        {
            "teacher_id":   row.TeacherSession.teacher_id,
            "session_id":   row.TeacherSession.session_id,
            "teacher_name": row.name,
        }
        for row in result.all()
    ]
