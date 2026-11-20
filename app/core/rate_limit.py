"""
Limitation de débit commune à toute l'API (rapport V2, APP-11).

- Une seule instance, compteurs dans Redis : avec N workers gunicorn, un
  stockage en mémoire multiplierait chaque limite par N.
- Clé = utilisateur authentifié quand un jeton d'accès lisible est présent,
  sinon adresse IP réelle (transmise par Caddy, voir --forwarded-allow-ips).
  Des agents derrière la même box 4G ou le même NAT d'opérateur ne se bloquent
  donc pas entre eux pendant une synchronisation.
- Redis indisponible : repli sur des compteurs en mémoire plutôt qu'une
  erreur 500 (swallow_errors + in_memory_fallback).
"""
from __future__ import annotations

from jose import JWTError
from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

from app.core.config import settings
from app.core.security import decode_token


def client_key(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        try:
            payload = decode_token(auth[7:].strip())
            if payload.get("type") == "access" and payload.get("sub"):
                return f"user:{payload['sub']}"
        except JWTError:
            pass
    return f"ip:{get_remote_address(request)}"


limiter = Limiter(
    key_func=client_key,
    default_limits=[settings.RATE_LIMIT_DEFAULT],
    storage_uri=settings.redis_url,
    in_memory_fallback_enabled=True,
    swallow_errors=True,
    headers_enabled=False,
)
