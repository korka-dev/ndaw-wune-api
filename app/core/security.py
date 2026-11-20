from datetime import datetime, timedelta, timezone
from typing import Literal
import logging
import uuid

import bcrypt
from jose import jwt

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
        # Identifiant unique : deux jetons émis la même seconde restent
        # distincts (rotation et révocation du refresh token, APP-10).
        "jti": uuid.uuid4().hex,
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


DOWNLOAD_TOKEN_EXPIRE_SECONDS = 120


def create_download_token(subject: str, resource_id: str) -> str:
    """
    Jeton de très courte durée (2 min), scopé à une seule ressource.
    Utilisé uniquement pour ouvrir un fichier dans une app externe
    (Linking.openURL côté mobile), où un header Authorization n'est pas
    possible — évite d'exposer le token d'accès complet (60 min, tout compte)
    dans une URL transmise à une app tierce.
    """
    expire = datetime.now(timezone.utc) + timedelta(seconds=DOWNLOAD_TOKEN_EXPIRE_SECONDS)
    payload = {
        "sub": subject,
        "type": "download",
        "resource_id": resource_id,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


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


class RevocationUnavailable(Exception):
    """Redis indisponible alors que la vérification de révocation est obligatoire."""


async def is_token_revoked(token: str, *, fail_closed: bool = False) -> bool:
    """
    Retourne True si le token est dans la blacklist Redis.

    Politique en cas d'indisponibilité Redis (APP-10), documentée :
    - jetons d'accès (fail_closed=False) : on laisse passer. Leur durée est
      courte (ACCESS_TOKEN_EXPIRE_MINUTES) et les révocations par changement ou
      réinitialisation de mot de passe reposent sur tokens_valid_from, stocké en
      base, donc toujours appliquées ;
    - refresh tokens (fail_closed=True) : RevocationUnavailable, la route
      répond 503 et le client réessaie sans perdre sa session. Un jeton longue
      durée dont la révocation est invérifiable n'est jamais échangé.
    """
    from app.core.redis import get_redis
    try:
        redis = await get_redis()
        return await redis.exists(f"{_BLACKLIST_PREFIX}{token}") > 0
    except Exception as exc:
        logger.warning("[Security] Redis indisponible pour vérifier la blacklist : %s", exc)
        if fail_closed:
            raise RevocationUnavailable() from exc
        return False


_ROTATED_PREFIX = "rotated_refresh:"


async def mark_refresh_rotated(token: str, grace_seconds: int) -> bool:
    """
    Enregistre l'échange d'un refresh token (rotation, APP-10).

    Retourne True si l'échange est autorisé : premier usage, ou nouvel usage dans
    le délai de grâce (réponse perdue, requêtes simultanées). False si le jeton a
    déjà été échangé avant ce délai : il est alors révoqué définitivement.
    Lève RevocationUnavailable si Redis ne répond pas.
    """
    from app.core.redis import get_redis
    try:
        payload = decode_token(token)
        now = int(datetime.now(timezone.utc).timestamp())
        ttl = max(1, int(payload.get("exp", now + 1)) - now)
        redis = await get_redis()
        key = f"{_ROTATED_PREFIX}{token}"
        if await redis.set(key, str(now), ex=ttl, nx=True):
            return True
        first_use = int(await redis.get(key) or now)
    except RevocationUnavailable:
        raise
    except Exception as exc:
        logger.warning("[Security] Redis indisponible pour la rotation du refresh : %s", exc)
        raise RevocationUnavailable() from exc
    if now - first_use <= grace_seconds:
        return True
    await revoke_token(token)
    return False
