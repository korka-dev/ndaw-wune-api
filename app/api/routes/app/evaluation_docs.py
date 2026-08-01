"""App mobile — Dossier d'évaluation du superviseur (imposé par sa langue d'enseignement)."""
from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from app.core.deps import DB, SuperviseurUser
from app.core.langue import langue_matches
from app.models.evaluation_doc import EvaluationDoc
from app.services.supervisor_service import resolve_supervisor_langue

logger = logging.getLogger(__name__)

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
async def list_active_docs(current_user: SuperviseurUser, db: DB) -> list[EvaluationDocOut]:
    """
    Retourne le(s) dossier(s) d'évaluation de la langue d'enseignement du
    superviseur — jamais ceux des autres langues.

    Le superviseur ne choisit pas sa langue : elle est imposée par l'école à
    laquelle il est assigné. Un superviseur en école wolof reçoit le dossier
    wolof, en école seereer le dossier seereer, etc.

    La comparaison passe par canonical_langue : casse, accents et orthographes
    connues sont neutralisés ("Sérère" et "seereer" désignent la même langue).
    """
    langue = await resolve_supervisor_langue(db, current_user)
    if not langue:
        logger.warning(
            "[EvaluationDocs] Langue d'enseignement indéterminable pour le "
            "superviseur %s — aucun dossier renvoyé.", current_user.id,
        )
        return []

    rows = (await db.execute(
        select(EvaluationDoc)
        .where(EvaluationDoc.is_active.is_(True))
        .order_by(EvaluationDoc.created_at)
    )).scalars().all()
    rows = [d for d in rows if langue_matches(d.langue, langue)]

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
