from __future__ import annotations

from fastapi import APIRouter

from app.core.deps import AdminUser, DB
from app.core.pagination import Page, Pagination
from app.schemas.audit_log import AuditLogResponse
from app.services import audit_service

router = APIRouter(prefix="/audit-logs", tags=["Admin — Historique"])


@router.get("", response_model=Page[AuditLogResponse])
async def list_audit_logs(db: DB, _: AdminUser, page: Pagination) -> Page[AuditLogResponse]:
    """Historique des modifications effectuées sur la plateforme."""
    total, items = await audit_service.list_audit_logs(db, page.skip, page.limit)
    return Page(total=total, skip=page.skip, limit=page.limit, items=items)
