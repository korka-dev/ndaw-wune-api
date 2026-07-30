from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from jose import JWTError
from sqlalchemy import select
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from app.core.config import settings
from app.core.database import engine, AsyncSessionLocal
from app.core.logging import setup_logging
from app.core.redis import close_redis, get_redis
from app.core.security import decode_token
from app.models.user import User
from app.api.router import api_router

# Configurer le logging avant tout le reste
setup_logging(is_production=settings.is_production)

logger = logging.getLogger(__name__)


# ── Cycle de vie de l'application ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("Démarrage de %s [%s]", settings.APP_NAME, settings.APP_ENV)
    await get_redis()
    yield
    logger.info("Arrêt de l'application.")
    await close_redis()
    await engine.dispose()


# ── Rate Limiter (protection brute force) ─────────────────────────────────────
# L'instance est partagée — les routes l'importent via app.state.limiter
limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])


# ── Instance FastAPI ───────────────────────────────────────────────────────────

app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    # En production : désactiver Swagger/ReDoc/OpenAPI pour ne pas exposer le contrat
    docs_url=settings.docs_url,
    redoc_url=settings.redoc_url,
    openapi_url=settings.openapi_url,
    lifespan=lifespan,
)


from fastapi.exceptions import RequestValidationError

# ── Wiring du rate limiter ─────────────────────────────────────────────────────
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ── Middlewares ────────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "X-Request-ID"],
    max_age=600,  # cache preflight 10 min
)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    """
    Ajoute un ID unique à chaque requête et logue le temps de réponse.
    Indispensable pour le débogage en production.
    """
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    start = time.perf_counter()

    response = await call_next(request)

    duration_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "%s %s → %d  (%.1f ms) [%s]",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
        request_id,
    )
    # Exposer l'ID dans la réponse pour faciliter le support
    response.headers["X-Request-ID"] = request_id
    return response


@app.middleware("http")
async def audit_log_middleware(request: Request, call_next):
    """
    Historique des modifications : enregistre toute action de création,
    modification ou suppression effectuée sur les routes d'administration,
    avec l'identité de l'utilisateur connecté.
    """
    response = await call_next(request)

    try:
        if (
            response.status_code < 400
            and request.method in ("POST", "PUT", "PATCH", "DELETE")
            and "/admin/" in request.url.path
            and not request.url.path.rstrip("/").endswith("/audit-logs")
        ):
            auth_header = request.headers.get("authorization", "")
            if auth_header.lower().startswith("bearer "):
                token = auth_header.split(" ", 1)[1]
                try:
                    payload = decode_token(token)
                    user_id = payload.get("sub")
                except JWTError:
                    user_id = None

                if user_id:
                    async with AsyncSessionLocal() as session:
                        result = await session.execute(select(User).where(User.id == user_id))
                        user = result.scalar_one_or_none()
                        if user is not None:
                            from app.services.audit_service import log_action
                            await log_action(
                                session,
                                user=user,
                                method=request.method,
                                path=request.url.path,
                            )
    except Exception:
        logger.exception("Échec de l'enregistrement de l'historique des modifications.")

    return response


# ── Gestionnaires d'erreurs globaux ─────────────────────────────────────────────

_SENSITIVE_BODY_KEYS = {
    "password", "new_password", "confirm_password", "old_password",
    "current_password", "secret", "secret_key", "token",
    "access_token", "refresh_token",
}


def _redact_body_for_log(body_bytes: bytes) -> str:
    """
    Retourne une version du corps de requête sûre à logger : les mots de
    passe et tokens sont remplacés par "***", tout le reste est conservé
    pour le débogage. Si le corps n'est pas du JSON exploitable (upload de
    fichier, etc.), on ne logue rien plutôt que de risquer de fuiter des
    données binaires ou non prévues.
    """
    import json

    try:
        parsed = json.loads(body_bytes.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return "<corps non-JSON — non loggé>"

    def redact(value):
        if isinstance(value, dict):
            return {
                k: ("***" if k.lower() in _SENSITIVE_BODY_KEYS else redact(v))
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [redact(v) for v in value]
        return value

    return json.dumps(redact(parsed), ensure_ascii=False)


def _safe_validation_errors(errors: list) -> list:
    """Rend les erreurs Pydantic v2 JSON-sérialisables (et sûres à logger/renvoyer).

    En Pydantic v2, le champ `ctx` peut contenir l'instance d'exception brute
    (ex. ValueError) qui n'est pas sérialisable par json.dumps().
    On convertit toute valeur qui est une Exception en sa repr string.

    Pydantic inclut aussi la valeur brute soumise dans `input` — pour un champ
    sensible (mot de passe, token…), cette valeur atterrirait en clair à la
    fois dans les logs ET dans la réponse HTTP 422 renvoyée au client.
    """
    safe = []
    for err in errors:
        safe_err = {}
        loc = err.get("loc") or ()
        is_sensitive_field = any(
            str(part).lower() in _SENSITIVE_BODY_KEYS for part in loc
        )
        for key, val in err.items():
            if key == "ctx" and isinstance(val, dict):
                safe_err[key] = {
                    k: str(v) if isinstance(v, Exception) else v
                    for k, v in val.items()
                }
            elif key == "input" and is_sensitive_field:
                safe_err[key] = "***"
            else:
                safe_err[key] = val
        safe.append(safe_err)
    return safe


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Loggue en détail les requêtes invalides pour simplifier le débogage."""
    body_bytes = b""
    try:
        body_bytes = await request.body()
    except Exception:
        pass

    errors = _safe_validation_errors(exc.errors())

    logger.error(
        "⚠️ Erreur de validation (422) sur %s %s [%s] :\n- Erreurs : %s\n- Body reçu (mots de passe/tokens masqués) : %s",
        request.method,
        request.url.path,
        getattr(request.state, "request_id", "—"),
        errors,
        _redact_body_for_log(body_bytes),
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": errors},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Capture les exceptions non gérées et retourne un 500 propre.
    En développement le message réel est exposé pour faciliter le débogage.
    En production on masque le détail pour ne pas exposer l'internals.
    """
    logger.exception(
        "Erreur non gérée sur %s %s [%s] : %r",
        request.method,
        request.url.path,
        getattr(request.state, "request_id", "—"),
        exc,
    )
    detail = (
        f"{type(exc).__name__}: {exc}"
        if settings.is_development
        else "Une erreur interne est survenue. Veuillez réessayer."
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": detail},
    )


# ── Routes ─────────────────────────────────────────────────────────────────────

app.include_router(api_router)


# ── Métriques Prometheus ────────────────────────────────────────────────────────
# Expose /metrics (latence, taux d'erreur, requêtes/s par endpoint). Jamais
# public : Caddy bloque explicitement ce chemin (voir Caddyfile.api) — seul
# Prometheus, sur le réseau Docker interne, y accède directement (backend:8000).
from prometheus_fastapi_instrumentator import Instrumentator  # noqa: E402

Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)


@app.get("/health", tags=["Health"], include_in_schema=False)
async def health() -> dict:
    """Endpoint de healthcheck pour Docker et le load balancer."""
    return {"status": "ok", "app": settings.APP_NAME, "env": settings.APP_ENV}
