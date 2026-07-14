"""Routes Admin — Logs d'utilisation de l'application mobile.

Routes :
  GET /admin/usage-logs        → liste paginée des événements
  GET /admin/usage-logs/stats  → agrégats par fonctionnalité et par rôle
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select

from app.core.deps import AdminUser, DB
from app.core.pagination import Page, Pagination
from app.models.usage_log import UsageLog

router = APIRouter(prefix="/usage-logs", tags=["Admin — Logs d'utilisation"])


class UsageLogOut(BaseModel):
    id:         str
    user_id:    Optional[str] = None
    user_name:  str
    user_role:  str
    feature:    str
    created_at: str


class FeatureStat(BaseModel):
    feature: str
    count:   int


class RoleFeatureStat(BaseModel):
    user_role: str
    feature:   str
    count:     int


class UsageStatsOut(BaseModel):
    total:       int
    by_feature:  list[FeatureStat]
    by_role:     list[RoleFeatureStat]


def _parse_date(value: Optional[str], field: str) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"{field} invalide (format YYYY-MM-DD).")


def _date_filters(query, date_from: Optional[date], date_to: Optional[date]):
    if date_from:
        query = query.where(UsageLog.created_at >= datetime.combine(date_from, time.min, tzinfo=timezone.utc))
    if date_to:
        query = query.where(UsageLog.created_at <= datetime.combine(date_to, time.max, tzinfo=timezone.utc))
    return query


@router.get("", response_model=Page[UsageLogOut])
async def list_usage_logs(
    db: DB,
    _: AdminUser,
    page: Pagination,
    feature: Optional[str] = None,
    user_role: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> Page[UsageLogOut]:
    d_from = _parse_date(date_from, "date_from")
    d_to   = _parse_date(date_to,   "date_to")

    base = select(UsageLog).order_by(UsageLog.created_at.desc())
    base = _date_filters(base, d_from, d_to)
    if feature:
        base = base.where(UsageLog.feature == feature)
    if user_role:
        base = base.where(UsageLog.user_role == user_role)

    total = (await db.execute(
        select(func.count()).select_from(base.subquery())
    )).scalar_one()

    rows = (await db.execute(base.offset(page.skip).limit(page.limit))).scalars().all()
    items = [
        UsageLogOut(
            id=str(r.id),
            user_id=str(r.user_id) if r.user_id else None,
            user_name=r.user_name,
            user_role=r.user_role,
            feature=r.feature,
            created_at=r.created_at.isoformat(),
        )
        for r in rows
    ]
    return Page(total=total, skip=page.skip, limit=page.limit, items=items)


@router.get("/stats", response_model=UsageStatsOut)
async def usage_stats(
    db: DB,
    _: AdminUser,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> UsageStatsOut:
    d_from = _parse_date(date_from, "date_from")
    d_to   = _parse_date(date_to,   "date_to")

    total_q = _date_filters(select(func.count()).select_from(UsageLog), d_from, d_to)
    total = (await db.execute(total_q)).scalar_one()

    feat_q = _date_filters(
        select(UsageLog.feature, func.count().label("count"))
        .group_by(UsageLog.feature)
        .order_by(func.count().desc()),
        d_from, d_to,
    )
    by_feature = [
        FeatureStat(feature=row.feature, count=row.count)
        for row in (await db.execute(feat_q)).all()
    ]

    role_q = _date_filters(
        select(UsageLog.user_role, UsageLog.feature, func.count().label("count"))
        .group_by(UsageLog.user_role, UsageLog.feature)
        .order_by(func.count().desc()),
        d_from, d_to,
    )
    by_role = [
        RoleFeatureStat(user_role=row.user_role, feature=row.feature, count=row.count)
        for row in (await db.execute(role_q)).all()
    ]

    return UsageStatsOut(total=total, by_feature=by_feature, by_role=by_role)
