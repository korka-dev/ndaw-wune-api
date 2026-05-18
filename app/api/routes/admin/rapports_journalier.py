"""Endpoints Admin — Rapports Journaliers."""
from __future__ import annotations

import csv
import io
import uuid
from typing import Optional

from fastapi import APIRouter, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core.deps import AdminUser, DB
from app.core.pagination import Page, Pagination
from app.models.rapport_journalier import RapportJournalier
from app.schemas.rapport_journalier import RapportJournalierResponse

router = APIRouter(prefix="/rapports/journalier", tags=["Admin — Rapports Journaliers"])


@router.get("", response_model=Page[RapportJournalierResponse])
async def list_rapports_journalier(
    db: DB,
    _: AdminUser,
    page: Pagination,
    teacher_id: Optional[uuid.UUID] = None,
) -> Page[RapportJournalierResponse]:
    base = (
        select(RapportJournalier)
        .order_by(RapportJournalier.date_rapport.desc(), RapportJournalier.created_at.desc())
    )
    if teacher_id:
        base = base.where(RapportJournalier.teacher_id == teacher_id)

    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    items = (await db.execute(base.offset(page.skip).limit(page.limit))).scalars().all()
    return Page(total=total, skip=page.skip, limit=page.limit, items=items)


@router.get("/export/csv")
async def export_csv(
    db: DB,
    _: AdminUser,
    teacher_id: Optional[uuid.UUID] = None,
) -> StreamingResponse:
    """Exporte tous les rapports journaliers en CSV."""
    q = select(RapportJournalier).options(selectinload(RapportJournalier.teacher))
    if teacher_id:
        q = q.where(RapportJournalier.teacher_id == teacher_id)
    rapports = (await db.execute(q.order_by(RapportJournalier.date_rapport))).scalars().all()

    output  = io.StringIO()
    writer  = csv.writer(output)
    headers = [
        "date_rapport", "tuteur", "ief", "commune", "ecole", "superviseur",
        "nb_absences", "semaine", "jour_cours", "directeur_venu", "besoin_appui",
        "commentaires", "soumis_en_offline",
    ]
    writer.writerow(headers)
    for r in rapports:
        writer.writerow([
            r.date_rapport, r.nom_tuteur, r.ief, r.commune, r.ecole, r.superviseur,
            r.nb_absences, r.semaine, r.jour_cours, r.directeur_venu, r.besoin_appui,
            r.commentaires, r.soumis_en_offline,
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=rapports_journalier.csv"},
    )
