"""Endpoints App mobile — Rapports Journaliers (soumission par le tuteur)."""
from __future__ import annotations

from fastapi import APIRouter, status

from app.core.deps import DB, TeacherUser
from app.core.pagination import Page, Pagination
from app.models.rapport_journalier import RapportJournalier
from app.schemas.rapport_journalier import RapportJournalierCreate, RapportJournalierResponse
from sqlalchemy import func, select

router = APIRouter(prefix="/rapports/journalier", tags=["App — Rapports Journaliers"])


@router.post("", response_model=RapportJournalierResponse, status_code=status.HTTP_201_CREATED)
async def submit_rapport_journalier(
    body: RapportJournalierCreate,
    current_user: TeacherUser,
    db: DB,
) -> RapportJournalierResponse:
    """Soumet un rapport journalier depuis l'app mobile (online ou offline)."""
    rapport = RapportJournalier(teacher_id=current_user.id, **body.model_dump())
    db.add(rapport)
    await db.flush()
    await db.refresh(rapport)
    return rapport


@router.get("", response_model=Page[RapportJournalierResponse])
async def list_rapports_journalier(
    current_user: TeacherUser,
    db: DB,
    page: Pagination,
) -> Page[RapportJournalierResponse]:
    """Historique paginé des rapports journaliers de l'utilisateur connecté."""
    base = (
        select(RapportJournalier)
        .where(RapportJournalier.teacher_id == current_user.id)
        .order_by(RapportJournalier.date_rapport.desc())
    )
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    items = (await db.execute(base.offset(page.skip).limit(page.limit))).scalars().all()
    return Page(total=total, skip=page.skip, limit=page.limit, items=items)
