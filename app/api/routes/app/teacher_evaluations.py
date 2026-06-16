"""Endpoint App mobile — Résultats d'évaluations pour l'enseignant.

Route :
  GET /app/teacher/evaluations → évaluations des élèves de cet enseignant
                                  faites par les superviseurs de terrain.
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from app.core.deps import DB, TeacherUser
from app.models.eleve import Eleve
from app.models.evaluation_eleve import EvaluationEleve
from app.models.user import User

router = APIRouter(prefix="/teacher", tags=["App — Enseignant"])


# ── Schémas ───────────────────────────────────────────────────────────────────

class EvalResultItem(BaseModel):
    eleve_id:        str
    nom:             str
    prenom:          Optional[str] = None
    classe:          str
    competence:      str
    resultat:        str            # acquis | en_cours | a_aider
    date_eval:       str            # YYYY-MM-DD
    commentaire:     Optional[str] = None
    superviseur_nom: Optional[str] = None


class TeacherEvaluationsPayload(BaseModel):
    evaluations: list[EvalResultItem]
    total:       int


# ── Route ─────────────────────────────────────────────────────────────────────

@router.get("/evaluations", response_model=TeacherEvaluationsPayload)
async def teacher_evaluations(
    current_user: TeacherUser,
    db: DB,
) -> TeacherEvaluationsPayload:
    """
    Retourne toutes les évaluations faites par les superviseurs sur les élèves
    des classes de cet enseignant.

    Logique :
      1. Récupérer les classes et l'école de l'enseignant.
      2. Trouver les élèves correspondants dans la table eleves.
      3. Charger leurs évaluations (toutes dates, tous superviseurs).
      4. Joindre les noms des élèves et des superviseurs pour l'affichage.
    """
    if not current_user.school_id or not current_user.classes:
        return TeacherEvaluationsPayload(evaluations=[], total=0)

    # 1. Élèves des classes de l'enseignant
    eleves_result = await db.execute(
        select(Eleve).where(
            Eleve.school_id == current_user.school_id,
            Eleve.classe.in_(current_user.classes),
            Eleve.statut == "actif",
        )
    )
    eleves = eleves_result.scalars().all()
    if not eleves:
        return TeacherEvaluationsPayload(evaluations=[], total=0)

    eleve_map: dict[uuid.UUID, Eleve] = {e.id: e for e in eleves}
    eleve_ids = list(eleve_map.keys())

    # 2. Évaluations de ces élèves (ordre : date desc puis élève)
    evals_result = await db.execute(
        select(EvaluationEleve)
        .where(EvaluationEleve.eleve_id.in_(eleve_ids))
        .order_by(
            EvaluationEleve.date_eval.desc(),
            EvaluationEleve.competence,
            EvaluationEleve.eleve_id,
        )
    )
    evals = evals_result.scalars().all()
    if not evals:
        return TeacherEvaluationsPayload(evaluations=[], total=0)

    # 3. Noms des superviseurs (batch)
    sup_ids = list({e.superviseur_id for e in evals})
    sups_result = await db.execute(select(User).where(User.id.in_(sup_ids)))
    sup_map: dict[uuid.UUID, str] = {
        u.id: u.name for u in sups_result.scalars().all()
    }

    # 4. Construire la réponse
    items: list[EvalResultItem] = []
    for ev in evals:
        eleve = eleve_map.get(ev.eleve_id)
        if eleve is None:
            continue
        items.append(EvalResultItem(
            eleve_id=str(ev.eleve_id),
            nom=eleve.nom,
            prenom=eleve.prenom,
            classe=eleve.classe,
            competence=ev.competence,
            resultat=ev.resultat,
            date_eval=ev.date_eval.isoformat(),
            commentaire=ev.commentaire,
            superviseur_nom=sup_map.get(ev.superviseur_id),
        ))

    return TeacherEvaluationsPayload(evaluations=items, total=len(items))
