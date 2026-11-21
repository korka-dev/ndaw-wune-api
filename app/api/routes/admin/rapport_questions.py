"""Routes admin — Gestion des questions complémentaires du rapport journalier.

Ces questions sont définies par l'admin et affichées dynamiquement dans le
formulaire de rapport journalier de l'app mobile (synchronisées via /app/sync).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select

from app.core.deps import AdminUser, DB
from app.core.redis import invalidate_sync_caches
from app.models.rapport_question import RapportQuestion, RapportQuestionVersion
from app.schemas.rapport_question import (
    RapportQuestionCreate,
    RapportQuestionResponse,
    RapportQuestionUpdate,
)

router = APIRouter(prefix="/rapport-questions", tags=["Admin — Questions de rapport"])

# Champs qui définissent le SENS d'une question (lot L4, D5.2 / REC13) : les
# modifier crée une nouvelle version ; ordre, obligation et activation non.
CHAMPS_DE_SENS = ("label", "type", "options", "cible")


def _figer_version(db: DB, obj: RapportQuestion) -> None:
    db.add(RapportQuestionVersion(
        question_id=obj.id, version=obj.version, label=obj.label,
        type=obj.type.value if hasattr(obj.type, "value") else obj.type,
        options=obj.options, cible=obj.cible.value if hasattr(obj.cible, "value") else obj.cible,
        valid_from=datetime.now(timezone.utc),
    ))


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
    obj = RapportQuestion(**body.model_dump(), version=1)
    db.add(obj)
    await db.flush()
    _figer_version(db, obj)
    await db.flush()
    await invalidate_sync_caches()
    return obj


@router.patch("/{question_id}", response_model=RapportQuestionResponse)
async def update_rapport_question(
    question_id: uuid.UUID, body: RapportQuestionUpdate, db: DB, _: AdminUser
) -> RapportQuestionResponse:
    obj = await _get_or_404(db, question_id)
    changes = body.model_dump(exclude_unset=True)
    def _val(v):
        return v.value if hasattr(v, "value") else v
    sens_modifie = any(
        f in changes and _val(changes[f]) != _val(getattr(obj, f)) for f in CHAMPS_DE_SENS
    )
    for field, value in changes.items():
        setattr(obj, field, value)
    if sens_modifie:
        # Nouvelle version : les réponses déjà reçues restent interprétées avec
        # la définition en vigueur à leur réception.
        obj.version += 1
        await db.flush()
        _figer_version(db, obj)
    await db.flush()
    await invalidate_sync_caches()
    return obj


@router.delete("/{question_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rapport_question(question_id: uuid.UUID, db: DB, _: AdminUser) -> Response:
    obj = await _get_or_404(db, question_id)
    await db.delete(obj)
    await invalidate_sync_caches()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
