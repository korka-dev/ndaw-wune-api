"""Endpoint App mobile — Résultats d'évaluations pour l'enseignant.

Route :
  GET /app/teacher/evaluations → évaluations des élèves de cet enseignant
                                  faites par les superviseurs de terrain.

Deux sources sont fusionnées, car les superviseurs évaluent de deux façons :
  • evaluations_eleves  → évaluation par compétence (libre, hors sujet)
  • evaluation_tirages  → évaluation sur un sujet créé par l'admin, avec des
    élèves tirés au sort (parcours principal de l'app superviseur)
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.deps import DB, TeacherUser
from app.models.eleve import Eleve
from app.models.evaluation_eleve import EvaluationEleve
from app.models.evaluation_tirage import EvaluationTirage
from app.models.user import User

router = APIRouter(prefix="/teacher", tags=["App — Enseignant"])

# Les tirages utilisent le vocabulaire du superviseur (réussi / intermédiaire /
# pas réussi) ; l'app enseignant affiche la même échelle à 3 niveaux avec son
# propre vocabulaire pédagogique. On convertit pour un affichage homogène.
_RESULTAT_TIRAGE_VERS_ENSEIGNANT = {
    "reussi":        "acquis",
    "intermediaire": "en_cours",
    "pas_reussi":    "a_aider",
}


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

    # 2. Évaluations par compétence
    evals = (await db.execute(
        select(EvaluationEleve)
        .where(EvaluationEleve.eleve_id.in_(eleve_ids))
        .order_by(
            EvaluationEleve.date_eval.desc(),
            EvaluationEleve.competence,
            EvaluationEleve.eleve_id,
        )
    )).scalars().all()

    # 3. Évaluations sur sujet (élèves tirés au sort) — uniquement celles
    #    réellement évaluées : un tirage sans résultat n'est pas une évaluation.
    tirages = (await db.execute(
        select(EvaluationTirage)
        .options(selectinload(EvaluationTirage.sujet))
        .where(
            EvaluationTirage.eleve_id.in_(eleve_ids),
            EvaluationTirage.resultat.isnot(None),
        )
        .order_by(EvaluationTirage.date_eval.desc())
    )).scalars().all()

    if not evals and not tirages:
        return TeacherEvaluationsPayload(evaluations=[], total=0)

    # 4. Noms des superviseurs des deux sources (une seule requête)
    sup_ids = {e.superviseur_id for e in evals}
    sup_ids |= {t.superviseur_id for t in tirages if t.superviseur_id}
    sup_map: dict[uuid.UUID, str] = {}
    if sup_ids:
        sup_map = {
            u.id: u.name
            for u in (await db.execute(select(User).where(User.id.in_(sup_ids)))).scalars().all()
        }

    # 5. Construire la réponse fusionnée
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

    for t in tirages:
        eleve = eleve_map.get(t.eleve_id)
        if eleve is None:
            continue
        items.append(EvalResultItem(
            eleve_id=str(t.eleve_id),
            nom=eleve.nom,
            prenom=eleve.prenom,
            classe=eleve.classe,
            # Le sujet joue le rôle de compétence évaluée (ex. « Lecture »)
            competence=(t.sujet.titre if t.sujet else "Évaluation"),
            resultat=_RESULTAT_TIRAGE_VERS_ENSEIGNANT.get(t.resultat or "", t.resultat or ""),
            date_eval=t.date_eval.isoformat() if t.date_eval else "",
            commentaire=t.commentaire,
            superviseur_nom=sup_map.get(t.superviseur_id) if t.superviseur_id else None,
        ))

    # Plus récentes d'abord, toutes sources confondues
    items.sort(key=lambda i: (i.date_eval, i.competence), reverse=True)

    return TeacherEvaluationsPayload(evaluations=items, total=len(items))
