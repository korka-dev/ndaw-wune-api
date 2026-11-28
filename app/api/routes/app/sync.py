from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from app.core.app_version import is_outdated
from app.core.deps import DB, CurrentUser, TeacherUser
from app.core.etag import reponse_avec_etag
from app.models.user import UserRole
from app.core.redis import get_redis
from app.core.config import settings
from app.schemas.sync import ParametresTerrain, SyncPayload
from app.services.sync_service import build_sync_payload
from app.services.terrain_service import enregistrer_contact, parametres_terrain

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sync", tags=["App — Sync"])

_CACHE_PREFIX = "sync:"


# L'espace inspecteur arrive avec la 1.4 : une version plus ancienne ouvre
# l'accueil tuteur (vide) à un inspecteur. On lui demande de mettre à jour.
VERSION_ESPACE_INSPECTEUR = "1.4.1"


@router.get("", response_model=SyncPayload)
async def sync(current_user: CurrentUser, db: DB, request: Request) -> SyncPayload:
    """
    Télécharge toutes les données nécessaires pour fonctionner hors-ligne.
    Le résultat est mis en cache Redis (TTL = SYNC_CACHE_TTL_SECONDS).
    Si Redis est indisponible, on construit le payload directement depuis la DB
    sans mettre en cache (dégradé mais fonctionnel).
    """
    if current_user.role == UserRole.inspecteur:
        version = request.headers.get("x-app-version")
        if is_outdated(version, VERSION_ESPACE_INSPECTEUR) or not version:
            return JSONResponse(status_code=status.HTTP_426_UPGRADE_REQUIRED, content={
                "detail": "APP_UPDATE_REQUIRED",
                "min_version": VERSION_ESPACE_INSPECTEUR,
                "latest_version": settings.LATEST_APP_VERSION or VERSION_ESPACE_INSPECTEUR,
            })
    if current_user.role != UserRole.enseignant:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Accès réservé aux enseignants.")

    cache_key = f"{_CACHE_PREFIX}{current_user.id}"

    # ── Lecture cache Redis ────────────────────────────────────────────────────
    try:
        redis  = await get_redis()
        cached = await redis.get(cache_key)
        if cached:
            return await _finaliser(db, current_user, request, SyncPayload.model_validate_json(cached))
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

    return await _finaliser(db, current_user, request, payload)


async def _finaliser(db, user, request: Request, payload: SyncPayload) -> Response:
    """Règles terrain toujours à jour (hors cache) et trace de la synchronisation (RET15).
    Réponse 304 si l'app a déjà ce contenu (ETag)."""
    payload.parametres_terrain = ParametresTerrain(**parametres_terrain())
    await enregistrer_contact(db, user, request.headers.get("x-app-version"),
                              payload.profile.minuteur_visible, payload.profile.app_access)
    return reponse_avec_etag(request, payload)


@router.post("/invalidate", status_code=status.HTTP_204_NO_CONTENT)
async def invalidate_cache(current_user: TeacherUser) -> Response:
    """Force une nouvelle synchronisation en supprimant le cache Redis de l'enseignant."""
    try:
        redis = await get_redis()
        await redis.delete(f"{_CACHE_PREFIX}{current_user.id}")
    except Exception as exc:
        logger.warning("[Sync] Redis indisponible (invalidation manuelle) : %s", exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
