"""App mobile — Dossiers d'évaluation actifs (lecture superviseur)."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from app.core.deps import DB, SuperviseurUser
from app.models.evaluation_doc import EvaluationDoc

router = APIRouter(prefix="/supervisor", tags=["App — Dossiers d'évaluation"])


class EvaluationDocOut(BaseModel):
    id:         str
    langue:     str
    titre:      str
    lettres:    list[str]
    syllabes:   list[str]
    mots:       list[str]
    operations: list[str]


@router.get("/evaluation-docs", response_model=list[EvaluationDocOut])
async def list_active_docs(_: SuperviseurUser, db: DB) -> list[EvaluationDocOut]:
    """Retourne les dossiers d'évaluation actifs, triés par date de création."""
    rows = (await db.execute(
        select(EvaluationDoc)
        .where(EvaluationDoc.is_active.is_(True))
        .order_by(EvaluationDoc.created_at)
    )).scalars().all()
    return [
        EvaluationDocOut(
            id=str(d.id),
            langue=d.langue,
            titre=d.titre,
            lettres=d.lettres or [],
            syllabes=d.syllabes or [],
            mots=d.mots or [],
            operations=d.operations or [],
        )
        for d in rows
    ]
