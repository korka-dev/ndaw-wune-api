"""Endpoints Admin — Rapports Journaliers."""
from __future__ import annotations

import csv
import io
import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, or_
from sqlalchemy.orm import selectinload

from app.core.deps import AdminUser, DB
from app.core.export_utils import build_csv_response
from app.core.pagination import Page, Pagination
from app.models.rapport_journalier import RapportJournalier
from app.models.user import User, UserRole
from app.schemas.rapport_journalier import RapportJournalierResponse

router = APIRouter(prefix="/rapports/journalier", tags=["Admin — Rapports Journaliers"])


@router.get("", response_model=Page[RapportJournalierResponse])
async def list_rapports_journalier(
    db: DB,
    _: AdminUser,
    page: Pagination,
    teacher_id:  Optional[uuid.UUID] = None,
    role:        Optional[UserRole]  = None,   # filtre auteur du rapport : enseignant / superviseur
    search:      Optional[str]       = None,   # recherche nom_tuteur / ecole / ief
    date_from:   Optional[date]      = None,
    date_to:     Optional[date]      = None,
    ief:         Optional[str]       = None,
) -> Page[RapportJournalierResponse]:
    base = (
        select(RapportJournalier)
        .order_by(RapportJournalier.date_rapport.desc(), RapportJournalier.created_at.desc())
    )
    if role:
        base = base.join(User, User.id == RapportJournalier.teacher_id).where(User.role == role)
    if teacher_id:
        base = base.where(RapportJournalier.teacher_id == teacher_id)
    if search:
        like = f"%{search}%"
        base = base.where(
            or_(
                RapportJournalier.nom_tuteur.ilike(like),
                RapportJournalier.ecole.ilike(like),
                RapportJournalier.ief.ilike(like),
                RapportJournalier.commune.ilike(like),
            )
        )
    if date_from:
        base = base.where(RapportJournalier.date_rapport >= date_from)
    if date_to:
        base = base.where(RapportJournalier.date_rapport <= date_to)
    if ief:
        base = base.where(RapportJournalier.ief.ilike(f"%{ief}%"))

    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    items = (await db.execute(base.offset(page.skip).limit(page.limit))).scalars().all()
    return Page(total=total, skip=page.skip, limit=page.limit, items=items)


@router.get("/export/csv")
async def export_csv(
    db: DB,
    _: AdminUser,
    teacher_id: Optional[uuid.UUID] = None,
    role:       Optional[UserRole]  = None,
    search:     Optional[str]       = None,
    date_from:  Optional[date]      = None,
    date_to:    Optional[date]      = None,
    ief:        Optional[str]       = None,
    fields:     Optional[str]       = None,
) -> StreamingResponse:
    """Exporte les rapports journaliers filtrés en CSV."""
    q = select(RapportJournalier).options(selectinload(RapportJournalier.teacher))
    if role:
        q = q.join(User, User.id == RapportJournalier.teacher_id).where(User.role == role)
    if teacher_id:
        q = q.where(RapportJournalier.teacher_id == teacher_id)
    if search:
        like = f"%{search}%"
        q = q.where(
            or_(
                RapportJournalier.nom_tuteur.ilike(like),
                RapportJournalier.ecole.ilike(like),
                RapportJournalier.ief.ilike(like),
            )
        )
    if date_from:
        q = q.where(RapportJournalier.date_rapport >= date_from)
    if date_to:
        q = q.where(RapportJournalier.date_rapport <= date_to)
    if ief:
        q = q.where(RapportJournalier.ief.ilike(f"%{ief}%"))

    rapports = (await db.execute(q.order_by(RapportJournalier.date_rapport))).scalars().all()

    columns = [
        ("date_rapport",            "date_rapport"),
        ("tuteur",                  "tuteur"),
        ("ief",                     "ief"),
        ("commune",                 "commune"),
        ("ecole",                   "ecole"),
        ("superviseur",             "superviseur"),
        ("nb_absences",             "nb_absences"),
        ("absents",                 "absents"),
        ("semaine",                 "semaine"),
        ("jour_cours",              "jour_cours"),
        ("difficultes",             "difficultes"),
        ("autres_difficultes",      "autres_difficultes"),
        ("description_difficultes", "description_difficultes"),
        ("directeur_venu",          "directeur_venu"),
        ("besoin_appui",            "besoin_appui"),
        ("domaines_appui",          "domaines_appui"),
        ("has_observations",        "has_observations"),
        ("commentaires",            "commentaires"),
        ("soumis_en_offline",       "soumis_en_offline"),
    ]
    rows = [
        [
            r.date_rapport, r.nom_tuteur, r.ief, r.commune, r.ecole, r.superviseur,
            r.nb_absences, r.absents, r.semaine, r.jour_cours,
            r.difficultes, r.autres_difficultes, r.description_difficultes,
            r.directeur_venu, r.besoin_appui, r.domaines_appui,
            r.has_observations, r.commentaires, r.soumis_en_offline,
        ]
        for r in rapports
    ]

    return build_csv_response(
        columns=columns,
        rows=rows,
        fields=fields,
        filename="rapports_journalier.csv",
    )
