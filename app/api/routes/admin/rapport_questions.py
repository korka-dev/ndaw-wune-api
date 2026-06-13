"""Routes admin — Gestion des questions complémentaires du rapport journalier.

Ces questions sont définies par l'admin et affichées dynamiquement dans le
formulaire de rapport journalier de l'app mobile (synchronisées via /app/sync).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select

from app.core.deps import AdminUser, DB
from app.models.rapport_question import RapportQuestion
from app.schemas.rapport_question import (
    RapportQuestionCreate,
    RapportQuestionResponse,
    RapportQuestionUpdate,
)

router = APIRouter(prefix="/rapport-questions", tags=["Admin — Questions de rapport"])


async def _get_or_404(db: DB, question_id: uuid.UUID) -> RapportQuestion:
    obj = (
        await db.execute(select(RapportQuestion).where(RapportQuestion.id == question_id))
    ).scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=404, detail="Question introuvable.")
    return obj


@router.get("", response_model=list[RapportQuestionResponse])
async def list_rapport_questions(db: DB, _: AdminUser) -> list[RapportQuestionResponse]:
    items = (
        await db.execute(select(RapportQuestion).order_by(RapportQuestion.ordre, RapportQuestion.created_at))
    ).scalars().all()
    return items


@router.post("", response_model=RapportQuestionResponse, status_code=status.HTTP_201_CREATED)
async def create_rapport_question(body: RapportQuestionCreate, db: DB, _: AdminUser) -> RapportQuestionResponse:
    obj = RapportQuestion(**body.model_dump())
    db.add(obj)
    await db.flush()
    return obj


@router.patch("/{question_id}", response_model=RapportQuestionResponse)
async def update_rapport_question(
    question_id: uuid.UUID, body: RapportQuestionUpdate, db: DB, _: AdminUser
) -> RapportQuestionResponse:
    obj = await _get_or_404(db, question_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(obj, field, value)
    await db.flush()
    return obj


@router.delete("/{question_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rapport_question(question_id: uuid.UUID, db: DB, _: AdminUser) -> Response:
    obj = await _get_or_404(db, question_id)
    await db.delete(obj)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
