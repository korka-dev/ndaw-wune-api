from datetime import datetime, timedelta, timezone
from typing import Optional, Literal
import logging

import bcrypt
from jose import JWTError, jwt

from app.core.config import settings

logger = logging.getLogger(__name__)

TokenType = Literal["access", "refresh"]

_BCRYPT_ROUNDS = 12  # coût CPU raisonnable (≈ 300 ms sur un serveur moderne)

# ── Préfixe Redis pour la blacklist de tokens révoqués ──────────────────────
_BLACKLIST_PREFIX = "revoked_token:"


def hash_password(password: str) -> str:
    """Retourne le hash bcrypt du mot de passe fourni."""
    salt = bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Vérifie un mot de passe en clair contre son hash bcrypt."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def create_token(
    subject: str,          # user id (UUID as str)
    token_type: TokenType,
    role: str,
    extra: dict | None = None,
) -> str:
    if token_type == "access":
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            days=settings.REFRESH_TOKEN_EXPIRE_DAYS
        )

    payload = {
        "sub": subject,
        "type": token_type,
        "role": role,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    if extra:
        payload.update(extra)

    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_token(token: str) -> dict:
    """Lève JWTError si invalide ou expiré."""
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])


def create_token_pair(user_id: str, role: str) -> dict:
    return {
        "access_token": create_token(user_id, "access", role),
        "refresh_token": create_token(user_id, "refresh", role),
        "token_type": "bearer",
    }


# ── Blacklist Redis (révocation de tokens) ───────────────────────────────────

async def revoke_token(token: str) -> None:
    """
    Ajoute un token à la blacklist Redis jusqu'à son expiration naturelle.
    Si Redis est indisponible, logue l'erreur sans planter (dégradé acceptable).
    """
    from app.core.redis import get_redis
    try:
        payload = decode_token(token)
        exp: int = payload.get("exp", 0)
        ttl = max(0, exp - int(datetime.now(timezone.utc).timestamp()))
        if ttl > 0:
            redis = await get_redis()
            await redis.set(f"{_BLACKLIST_PREFIX}{token}", "1", ex=ttl)
    except Exception as exc:
        logger.warning("[Security] Impossible de révoquer le token : %s", exc)


async def is_token_revoked(token: str) -> bool:
    """
    Retourne True si le token est dans la blacklist Redis.
    En cas d'indisponibilité Redis, retourne False (fail-open : l'authentification continue).
    """
    from app.core.redis import get_redis
    try:
        redis = await get_redis()
        return await redis.exists(f"{_BLACKLIST_PREFIX}{token}") > 0
    except Exception as exc:
        logger.warning("[Security] Redis indisponible pour vérifier la blacklist : %s", exc)
        return False
