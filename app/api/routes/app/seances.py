from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.core.deps import DB, TeacherUser
from app.core.pagination import Page, Pagination
from app.schemas.seance import SeanceFinish, SeancePauseBody, SeanceResumeBody, SeanceResponse, SeanceStart
from app.services import seance_service

router = APIRouter(prefix="/seances", tags=["App — Séances"])


# IMPORTANT : les routes statiques (/active, /start) DOIVENT être déclarées
# avant les routes paramétrées (/{seance_id}) pour éviter les conflits de routing.

@router.get("/active", response_model=SeanceResponse | None)
async def active_seance(current_user: TeacherUser, db: DB) -> SeanceResponse | None:
    """Retourne la séance en cours (utile lors du redémarrage de l'app)."""
    return await seance_service.get_active_seance(db, current_user.id)


@router.post("/start", response_model=SeanceResponse, status_code=status.HTTP_201_CREATED)
async def start_seance(body: SeanceStart, current_user: TeacherUser, db: DB) -> SeanceResponse:
    """Démarre le timer. Lève 409 si une séance est déjà en cours."""
    return await seance_service.start_seance(db, current_user.id, body)


@router.post("/{seance_id}/pause", response_model=SeanceResponse)
async def pause_seance(
    seance_id: uuid.UUID,
    body: SeancePauseBody,
    current_user: TeacherUser,
    db: DB,
) -> SeanceResponse:
    """Enregistre une mise en pause. Idempotent si déjà en pause."""
    return await seance_service.record_pause(db, seance_id, current_user.id, body)


@router.post("/{seance_id}/resume", response_model=SeanceResponse)
async def resume_seance(
    seance_id: uuid.UUID,
    body: SeanceResumeBody,
    current_user: TeacherUser,
    db: DB,
) -> SeanceResponse:
    """Ferme la dernière pause et recalcule le total des pauses."""
    return await seance_service.record_resume(db, seance_id, current_user.id, body)


@router.post("/{seance_id}/finish", response_model=SeanceResponse)
async def finish_seance(
    seance_id: uuid.UUID,
    body: SeanceFinish,
    current_user: TeacherUser,
    db: DB,
) -> SeanceResponse:
    """Arrête le timer et clôture la séance. Accepte les pauses offline."""
    return await seance_service.finish_seance(db, seance_id, current_user.id, body)


@router.get("", response_model=Page[SeanceResponse])
async def list_seances(current_user: TeacherUser, db: DB, page: Pagination) -> Page[SeanceResponse]:
    """Historique paginé des séances de l'enseignant connecté."""
    total, items = await seance_service.list_teacher_seances(db, current_user.id, page.skip, page.limit)
    return Page(total=total, skip=page.skip, limit=page.limit, items=items)
