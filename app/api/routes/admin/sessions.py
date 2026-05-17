from __future__ import annotations

import uuid

from fastapi import APIRouter, Response, status

from app.core.deps import AdminUser, DB
from app.core.pagination import Page, Pagination
from app.schemas.session import (
    SessionCreate,
    SessionResponse,
    SessionUpdate,
    TeacherAssignRequest,
    TeacherSessionResponse,
)
from app.services import session_service

router = APIRouter(prefix="/sessions", tags=["Admin — Sessions"])


@router.get("", response_model=Page[SessionResponse])
async def list_sessions(db: DB, _: AdminUser, page: Pagination) -> Page[SessionResponse]:
    total, items = await session_service.list_sessions(db, page.skip, page.limit)
    return Page(total=total, skip=page.skip, limit=page.limit, items=items)


@router.post("", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(body: SessionCreate, db: DB, _: AdminUser) -> SessionResponse:
    return await session_service.create_session(db, body)


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(session_id: uuid.UUID, db: DB, _: AdminUser) -> SessionResponse:
    return await session_service.get_by_id(db, session_id)


@router.patch("/{session_id}", response_model=SessionResponse)
async def update_session(session_id: uuid.UUID, body: SessionUpdate, db: DB, _: AdminUser) -> SessionResponse:
    return await session_service.update_session(db, session_id, body)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(session_id: uuid.UUID, db: DB, _: AdminUser) -> Response:
    await session_service.delete_session(db, session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{session_id}/activate", response_model=SessionResponse)
async def activate_session(session_id: uuid.UUID, db: DB, _: AdminUser) -> SessionResponse:
    """Active cette session et désactive toutes les autres (2 requêtes SQL, pas N)."""
    return await session_service.activate_session(db, session_id)


# ── Assignation enseignants ───────────────────────────────────────────────────

@router.post("/{session_id}/teachers", status_code=status.HTTP_204_NO_CONTENT)
async def assign_teachers(
    session_id: uuid.UUID,
    body: TeacherAssignRequest,
    db: DB,
    _: AdminUser,
) -> Response:
    await session_service.assign_teachers(db, session_id, body.teacher_ids)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{session_id}/teachers", response_model=list[TeacherSessionResponse])
async def list_session_teachers(
    session_id: uuid.UUID,
    db: DB,
    _: AdminUser,
) -> list[TeacherSessionResponse]:
    rows = await session_service.list_session_teachers(db, session_id)
    return [TeacherSessionResponse(**r) for r in rows]
