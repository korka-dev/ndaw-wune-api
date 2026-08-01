"""App mobile — Dossier d'évaluation du superviseur (imposé par sa langue d'enseignement)."""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select

from app.core.deps import DB, SuperviseurUser
from app.models.evaluation_doc import EvaluationDoc
from app.models.school import School
from app.models.user import User

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


async def _langue_superviseur(db, supervisor: User) -> str | None:
    """
    Détermine la langue d'enseignement du superviseur.

    1. Langue de son école de rattachement (cas normal).
    2. À défaut, langue des écoles de ses enseignants assignés (leurs UUIDs sont
       stockés dans supervisor.classes) — couvre un superviseur sans école
       rattachée mais affecté à des enseignants.
    Retourne None si rien n'est déterminable : dans ce cas aucun dossier n'est
    proposé, car laisser choisir la langue risquerait de faire évaluer un élève
    avec le support d'une autre langue.
    """
    if supervisor.school and supervisor.school.langue:
        return supervisor.school.langue.strip()

    teacher_ids: list[uuid.UUID] = []
    for raw in (supervisor.classes or []):
        try:
            teacher_ids.append(uuid.UUID(str(raw)))
        except (ValueError, AttributeError):
            continue
    if not teacher_ids:
        return None

    langues = (await db.execute(
        select(School.langue)
        .join(User, User.school_id == School.id)
        .where(User.id.in_(teacher_ids), School.langue.isnot(None))
        .distinct()
    )).scalars().all()

    valeurs = {l.strip().lower() for l in langues if l and l.strip()}
    if len(valeurs) == 1:
        return valeurs.pop()
    if len(valeurs) > 1:
        logger.warning(
            "[EvaluationDocs] Superviseur %s supervise plusieurs langues (%s) — "
            "aucun dossier proposé, rattachez-le à une école.",
            supervisor.id, ", ".join(sorted(valeurs)),
        )
    return None


@router.get("/evaluation-docs", response_model=list[EvaluationDocOut])
async def list_active_docs(current_user: SuperviseurUser, db: DB) -> list[EvaluationDocOut]:
    """
    Retourne le(s) dossier(s) d'évaluation de la langue d'enseignement du
    superviseur — jamais ceux des autres langues.

    Le superviseur ne choisit pas sa langue : elle est imposée par l'école à
    laquelle il est assigné. Un superviseur en école wolof reçoit le dossier
    wolof, en école seereer le dossier seereer, etc.

    La comparaison est insensible à la casse : les écoles enregistrent la langue
    en minuscules ("wolof") alors que les dossiers la capitalisent ("Wolof").
    """
    langue = await _langue_superviseur(db, current_user)
    if not langue:
        logger.warning(
            "[EvaluationDocs] Langue d'enseignement indéterminable pour le "
            "superviseur %s — aucun dossier renvoyé.", current_user.id,
        )
        return []

    rows = (await db.execute(
        select(EvaluationDoc)
        .where(
            EvaluationDoc.is_active.is_(True),
            func.lower(EvaluationDoc.langue) == func.lower(langue),
        )
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
