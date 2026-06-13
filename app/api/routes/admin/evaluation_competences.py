"""Routes admin — Gestion des compétences d'évaluation.

Ces compétences sont définies par l'admin et affichées dynamiquement dans
l'écran d'évaluation du superviseur (app mobile), synchronisées via /app/sync.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select

from app.core.deps import AdminUser, DB
from app.models.evaluation_competence import EvaluationCompetence
from app.schemas.evaluation_competence import (
    EvaluationCompetenceCreate,
    EvaluationCompetenceResponse,
    EvaluationCompetenceUpdate,
    slugify,
)

router = APIRouter(prefix="/evaluation-competences", tags=["Admin — Compétences d'évaluation"])


async def _get_or_404(db: DB, competence_id: uuid.UUID) -> EvaluationCompetence:
    obj = (
        await db.execute(select(EvaluationCompetence).where(EvaluationCompetence.id == competence_id))
    ).scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=404, detail="Compétence introuvable.")
    return obj


async def _unique_code(db: DB, base: str, exclude_id: uuid.UUID | None = None) -> str:
    code = base
    i = 1
    while True:
        q = select(EvaluationCompetence).where(EvaluationCompetence.code == code)
        if exclude_id:
            q = q.where(EvaluationCompetence.id != exclude_id)
        existing = (await db.execute(q)).scalar_one_or_none()
        if existing is None:
            return code
        i += 1
        code = f"{base}_{i}"


@router.get("", response_model=list[EvaluationCompetenceResponse])
async def list_evaluation_competences(db: DB, _: AdminUser) -> list[EvaluationCompetenceResponse]:
    items = (
        await db.execute(
            select(EvaluationCompetence).order_by(EvaluationCompetence.ordre, EvaluationCompetence.created_at)
        )
    ).scalars().all()
    return items


@router.post("", response_model=EvaluationCompetenceResponse, status_code=status.HTTP_201_CREATED)
async def create_evaluation_competence(
    body: EvaluationCompetenceCreate, db: DB, _: AdminUser
) -> EvaluationCompetenceResponse:
    code = await _unique_code(db, body.code.strip() if body.code else slugify(body.label))
    obj = EvaluationCompetence(
        label=body.label, code=code, active=body.active, ordre=body.ordre,
    )
    db.add(obj)
    await db.flush()
    return obj


@router.patch("/{competence_id}", response_model=EvaluationCompetenceResponse)
async def update_evaluation_competence(
    competence_id: uuid.UUID, body: EvaluationCompetenceUpdate, db: DB, _: AdminUser
) -> EvaluationCompetenceResponse:
    obj = await _get_or_404(db, competence_id)
    data = body.model_dump(exclude_unset=True)
    if "code" in data and data["code"]:
        data["code"] = await _unique_code(db, data["code"].strip(), exclude_id=competence_id)
    elif "code" in data:
        data.pop("code")
    for field, value in data.items():
        setattr(obj, field, value)
    await db.flush()
    return obj


@router.delete("/{competence_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_evaluation_competence(competence_id: uuid.UUID, db: DB, _: AdminUser) -> Response:
    obj = await _get_or_404(db, competence_id)
    await db.delete(obj)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
