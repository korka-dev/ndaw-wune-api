"""
Endpoints Admin — Suivi des évaluations.
Permet de consulter les évaluations d'élèves soumises par les superviseurs de terrain.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.core.deps import AdminUser, DB
from app.core.pagination import Page, Pagination
from app.models.eleve import Eleve
from app.models.evaluation_eleve import EvaluationEleve
from app.models.user import User, UserRole

router = APIRouter(prefix="/suivi-evaluations", tags=["Admin — Suivi Évaluations"])


# ── Schémas ────────────────────────────────────────────────────────────────────

class EvaluationItem(BaseModel):
    id:             str
    superviseur_id: str
    superviseur:    str
    eleve_id:       str
    eleve:          str
    classe:         Optional[str] = None
    school_name:    Optional[str] = None
    competence:     str
    resultat:       str
    date_eval:      date
    commentaire:    Optional[str] = None
    created_at:     str

    model_config = {"from_attributes": True}


class SuperviseurOption(BaseModel):
    id:                  str
    name:                str
    school_name:         Optional[str] = None
    total:               int           = 0   # évaluations toutes sessions confondues
    evaluations_semaine: int           = 0   # évaluations depuis lundi (semaine en cours)
    last_eval_date:      Optional[date] = None


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/superviseurs", response_model=list[SuperviseurOption])
async def list_evaluateurs(db: DB, _: AdminUser) -> list[SuperviseurOption]:
    """
    Liste de TOUS les superviseurs (pas seulement ceux ayant déjà évalué), avec
    leur total d'évaluations et leur activité de la semaine en cours — pour
    repérer d'un coup d'œil qui n'a pas fait sa supervision cette semaine.
    """
    superviseurs = (await db.execute(
        select(User)
        .options(selectinload(User.school))
        .where(User.role == UserRole.superviseur)
        .order_by(User.name)
    )).scalars().all()

    # Totaux + dernière date, groupés par superviseur (1 requête)
    totals = (await db.execute(
        select(
            EvaluationEleve.superviseur_id,
            func.count().label("total"),
            func.max(EvaluationEleve.date_eval).label("last_date"),
        ).group_by(EvaluationEleve.superviseur_id)
    )).all()
    totals_map = {r.superviseur_id: (r.total, r.last_date) for r in totals}

    # Évaluations depuis lundi de la semaine en cours (1 requête)
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    week_counts = (await db.execute(
        select(EvaluationEleve.superviseur_id, func.count().label("total"))
        .where(EvaluationEleve.date_eval >= week_start)
        .group_by(EvaluationEleve.superviseur_id)
    )).all()
    week_map = {r.superviseur_id: r.total for r in week_counts}

    return [
        SuperviseurOption(
            id=str(sup.id),
            name=sup.name,
            school_name=sup.school.name if sup.school else None,
            total=totals_map.get(sup.id, (0, None))[0],
            evaluations_semaine=week_map.get(sup.id, 0),
            last_eval_date=totals_map.get(sup.id, (0, None))[1],
        )
        for sup in superviseurs
    ]


@router.get("", response_model=Page[EvaluationItem])
async def list_suivi_evaluations(
    db:             DB,
    _:              AdminUser,
    page:           Pagination,
    superviseur_id: Optional[uuid.UUID] = None,
    resultat:       Optional[str]       = None,   # acquis | en_cours | a_aider
    classe:         Optional[str]       = None,
    search:         Optional[str]       = None,   # recherche élève / superviseur / compétence
    date_from:      Optional[date]      = None,
    date_to:        Optional[date]      = None,
) -> Page[EvaluationItem]:
    """Liste paginée des évaluations d'élèves soumises par les superviseurs."""
    base = (
        select(EvaluationEleve)
        .options(
            selectinload(EvaluationEleve.superviseur),
            selectinload(EvaluationEleve.eleve).selectinload(Eleve.school),
        )
        .join(User, User.id == EvaluationEleve.superviseur_id)
        .join(Eleve, Eleve.id == EvaluationEleve.eleve_id)
        .order_by(EvaluationEleve.date_eval.desc(), EvaluationEleve.created_at.desc())
    )

    if superviseur_id:
        base = base.where(EvaluationEleve.superviseur_id == superviseur_id)
    if resultat:
        base = base.where(EvaluationEleve.resultat == resultat)
    if classe:
        base = base.where(Eleve.classe == classe)
    if date_from:
        base = base.where(EvaluationEleve.date_eval >= date_from)
    if date_to:
        base = base.where(EvaluationEleve.date_eval <= date_to)
    if search:
        like = f"%{search}%"
        base = base.where(
            or_(
                User.name.ilike(like),
                Eleve.nom.ilike(like),
                Eleve.prenom.ilike(like),
                EvaluationEleve.competence.ilike(like),
            )
        )

    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (await db.execute(base.offset(page.skip).limit(page.limit))).scalars().all()

    items: list[EvaluationItem] = []
    for e in rows:
        eleve = e.eleve
        items.append(EvaluationItem(
            id=str(e.id),
            superviseur_id=str(e.superviseur_id),
            superviseur=e.superviseur.name if e.superviseur else "—",
            eleve_id=str(e.eleve_id),
            eleve=f"{eleve.prenom} {eleve.nom}".strip() if eleve and eleve.prenom else (eleve.nom if eleve else "—"),
            classe=eleve.classe if eleve else None,
            school_name=eleve.school.name if eleve and eleve.school else None,
            competence=e.competence,
            resultat=e.resultat,
            date_eval=e.date_eval,
            commentaire=e.commentaire,
            created_at=e.created_at.isoformat(),
        ))

    return Page(total=total, skip=page.skip, limit=page.limit, items=items)
