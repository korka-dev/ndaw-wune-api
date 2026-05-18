from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from app.core.config import settings
from app.core.database import engine
from app.core.logging import setup_logging
from app.core.redis import close_redis, get_redis
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
    allow_methods=["*"],
    allow_headers=["*"],
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


# ── Gestionnaires d'erreurs globaux ─────────────────────────────────────────────

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Loggue en détail les requêtes invalides pour simplifier le débogage."""
    body_bytes = b""
    try:
        body_bytes = await request.body()
    except Exception:
        pass
    
    logger.error(
        "⚠️ Erreur de validation (422) sur %s %s [%s] :\n- Erreurs : %s\n- Body reçu : %s",
        request.method,
        request.url.path,
        getattr(request.state, "request_id", "—"),
        exc.errors(),
        body_bytes.decode("utf-8", errors="replace"),
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": exc.errors()},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Capture les exceptions non gérées et retourne un 500 propre
    sans exposer la stack trace au client.
    """
    logger.exception(
        "Erreur non gérée sur %s %s [%s]",
        request.method,
        request.url.path,
        getattr(request.state, "request_id", "—"),
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Une erreur interne est survenue. Veuillez réessayer."},
    )


# ── Routes ─────────────────────────────────────────────────────────────────────

app.include_router(api_router)


@app.get("/health", tags=["Health"], include_in_schema=False)
async def health() -> dict:
    """Endpoint de healthcheck pour Docker et le load balancer."""
    return {"status": "ok", "app": settings.APP_NAME, "env": settings.APP_ENV}
