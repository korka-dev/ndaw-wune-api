from __future__ import annotations

import logging

from fastapi import APIRouter, Response, status

from app.core.deps import DB, TeacherUser
from app.core.redis import get_redis
from app.core.config import settings
from app.schemas.sync import SyncPayload
from app.services.sync_service import build_sync_payload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sync", tags=["App — Sync"])

_CACHE_PREFIX = "sync:"


@router.get("", response_model=SyncPayload)
async def sync(current_user: TeacherUser, db: DB) -> SyncPayload:
    """
    Télécharge toutes les données nécessaires pour fonctionner hors-ligne.
    Le résultat est mis en cache Redis (TTL = SYNC_CACHE_TTL_SECONDS).
    Si Redis est indisponible, on construit le payload directement depuis la DB
    sans mettre en cache (dégradé mais fonctionnel).
    """
    cache_key = f"{_CACHE_PREFIX}{current_user.id}"

    # ── Lecture cache Redis ────────────────────────────────────────────────────
    try:
        redis  = await get_redis()
        cached = await redis.get(cache_key)
        if cached:
            return SyncPayload.model_validate_json(cached)
    except Exception as exc:
        logger.warning("[Sync] Redis indisponible (lecture) : %s — fallback DB", exc)
        redis = None  # type: ignore[assignment]

    # ── Construction depuis la DB ─────────────────────────────────────────────
    payload = await build_sync_payload(db, current_user)

    # ── Mise en cache Redis (best-effort) ──────────────────────────────────────
    if redis is not None:
        try:
            await redis.set(cache_key, payload.model_dump_json(), ex=settings.SYNC_CACHE_TTL_SECONDS)
        except Exception as exc:
            logger.warning("[Sync] Redis indisponible (écriture) : %s", exc)

    return payload


@router.post("/invalidate", status_code=status.HTTP_204_NO_CONTENT)
async def invalidate_cache(current_user: TeacherUser) -> Response:
    """Force une nouvelle synchronisation en supprimant le cache Redis de l'enseignant."""
    try:
        redis = await get_redis()
        await redis.delete(f"{_CACHE_PREFIX}{current_user.id}")
    except Exception as exc:
        logger.warning("[Sync] Redis indisponible (invalidation manuelle) : %s", exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
