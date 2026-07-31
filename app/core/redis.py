from __future__ import annotations

import logging

import redis.asyncio as aioredis

from app.core.config import settings

logger = logging.getLogger(__name__)

_redis: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    """
    Retourne le pool Redis singleton.
    Appelé une seule fois au démarrage de l'application (lifespan).
    """
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            max_connections=settings.REDIS_MAX_CONNECTIONS,
            socket_connect_timeout=5,
            socket_timeout=5,
            retry_on_timeout=True,
        )
        # Vérification au démarrage
        await _redis.ping()
        logger.info("Connexion Redis établie.")
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
        logger.info("Connexion Redis fermée.")


async def invalidate_sync_caches() -> None:
    """
    Supprime tous les caches de synchronisation (clés sync:* — enseignants
    et superviseurs) afin que les mobiles reçoivent des données fraîches au
    prochain appel /app/sync ou /app/supervisor/sync.
    À appeler après toute modification admin de données synchronisées
    (planning, questions de rapport, difficultés, config de progression…).
    """
    try:
        redis = await get_redis()
        keys  = await redis.keys("sync:*")
        if keys:
            deleted = await redis.delete(*keys)
            logger.info("[Cache] Cache Redis invalidé : %d clé(s) supprimée(s)", deleted)
        else:
            logger.info("[Cache] Aucune clé sync:* à supprimer")
    except Exception as exc:
        logger.error(
            "[Cache] ⚠️  Impossible d'invalider le cache Redis : %s. "
            "Les mobiles recevront les nouvelles données au plus tard à l'expiration du TTL.",
            exc,
        )
