from __future__ import annotations

from fastapi import APIRouter, status

from app.core.deps import DB, TeacherUser
from app.core.pagination import Page, Pagination
from app.schemas.seance import RapportCreate, RapportResponse
from app.services import seance_service

router = APIRouter(prefix="/rapports", tags=["App — Rapports"])


@router.post("", response_model=RapportResponse, status_code=status.HTTP_201_CREATED)
async def submit_rapport(body: RapportCreate, current_user: TeacherUser, db: DB) -> RapportResponse:
    """
    Soumet un rapport de séance.
    Peut être appelé après reconnexion si rédigé hors-ligne (soumis_en_offline=True).
    """
    return await seance_service.submit_rapport(db, current_user.id, body)


@router.get("", response_model=Page[RapportResponse])
async def list_rapports(current_user: TeacherUser, db: DB, page: Pagination) -> Page[RapportResponse]:
    """Historique paginé des rapports de l'enseignant connecté."""
    total, items = await seance_service.list_teacher_rapports(db, current_user.id, page.skip, page.limit)
    return Page(total=total, skip=page.skip, limit=page.limit, items=items)
