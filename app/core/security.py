from datetime import datetime, timedelta, timezone
from typing import Optional, Literal

import bcrypt
from jose import JWTError, jwt

from app.core.config import settings

TokenType = Literal["access", "refresh"]

_BCRYPT_ROUNDS = 12  # coût CPU raisonnable (≈ 300 ms sur un serveur moderne)


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
