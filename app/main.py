from __future__ import annotations

import logging
import re
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.core.security import JWTError
from sqlalchemy import select
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.core.upload_utils import ContentLengthLimitMiddleware

from app.core.app_version import is_outdated
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
    if settings.secret_key_is_compromised and not settings.is_development:
        # APP-04 : pas d'arrêt brutal (les agents terrain seraient tous déconnectés
        # sans préparation) mais alerte explicite à chaque démarrage. La rotation
        # se fait de façon coordonnée par l'exploitant (voir docs/SECURITE_EXPLOITATION.md).
        logger.critical(
            "SECRET_KEY compromise : cette clé a été publiée dans l'historique git. "
            "Générez une nouvelle clé pour l'environnement %s et planifiez la rotation.",
            settings.APP_ENV,
        )
    await get_redis()
    # APP-21 : clôture périodique des séances oubliées, indépendante des pages.
    maintenance_task = None
    if settings.SEANCE_CLEANUP_INTERVAL_SECONDS > 0:
        import asyncio
        from app.services.maintenance import periodic_maintenance
        maintenance_task = asyncio.create_task(periodic_maintenance())
    yield
    if maintenance_task is not None:
        maintenance_task.cancel()
    logger.info("Arrêt de l'application.")
    await close_redis()
    await engine.dispose()


# ── Rate Limiter (APP-11) ─────────────────────────────────────────────────────
# Instance unique (Redis, clé utilisateur ou IP) : voir app/core/rate_limit.py.
from app.core.rate_limit import limiter  # noqa: E402


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
from app.core.journal import champs_en_erreur, erreurs_validation, pile, resume_erreur

# ── Wiring du rate limiter ─────────────────────────────────────────────────────
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
# Applique la limite par défaut à toutes les routes (auparavant déclarée mais
# jamais câblée, faute de middleware).
app.add_middleware(SlowAPIMiddleware)


# ── Middlewares ────────────────────────────────────────────────────────────────

# APP-39 : corps démesuré refusé avant lecture (CORS reste la couche externe).
app.add_middleware(ContentLengthLimitMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "X-Request-ID", "X-App-Version"],
    # Lisibles par le dashboard : référence support (APP-45) et nom des exports.
    expose_headers=["X-Request-ID", "X-Export-Id", "Content-Disposition"],
    max_age=600,  # cache preflight 10 min
)


_REQUEST_ID_VALIDE = re.compile(r"^[A-Za-z0-9-]{8,64}$")


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    """
    Ajoute un ID unique à chaque requête et logue le temps de réponse.
    Indispensable pour le débogage en production.
    """
    # APP-45 : un identifiant fourni par le client (X-Request-ID) est repris
    # s'il est bien formé, pour relier l'erreur affichée au journal serveur.
    fourni = request.headers.get("x-request-id", "")
    request_id = fourni if _REQUEST_ID_VALIDE.match(fourni) else str(uuid.uuid4())
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
async def app_version_middleware(request: Request, call_next):
    """APP-46 : refuse (426) une version mobile plus ancienne que MIN_APP_VERSION."""
    if settings.MIN_APP_VERSION and request.url.path.startswith("/api/v1/app/"):
        client_version = request.headers.get("x-app-version")
        if is_outdated(client_version, settings.MIN_APP_VERSION):
            return JSONResponse(
                status_code=status.HTTP_426_UPGRADE_REQUIRED,
                content={
                    "detail": "APP_UPDATE_REQUIRED",
                    "min_version": settings.MIN_APP_VERSION,
                    "latest_version": settings.LATEST_APP_VERSION or settings.MIN_APP_VERSION,
                },
            )
    return await call_next(request)


@app.middleware("http")
async def audit_log_middleware(request: Request, call_next):
    """
    Historique des modifications : enregistre toute action de création,
    modification ou suppression effectuée sur les routes d'administration,
    avec l'identité de l'utilisateur connecté.
    """
    from app.services.audit_service import deja_journalise, fermer_contexte, ouvrir_contexte

    _, jeton = ouvrir_contexte(request.state)
    try:
        response = await call_next(request)
        precise = deja_journalise()  # la route a déjà écrit une entrée précise (APP-13)
    finally:
        fermer_contexte(jeton)

    try:
        if (
            not precise
            and response.status_code < 400
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
                                request_id=getattr(request.state, "request_id", None),
                            )
    except Exception:
        logger.exception("Échec de l'enregistrement de l'historique des modifications.")

    return response


# ── Gestionnaires d'erreurs globaux ─────────────────────────────────────────────

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Requête invalide (422) — APP-13 : ni le corps reçu ni les valeurs saisies
    ne sont journalisés ou renvoyés ; seulement les champs, les règles en échec
    et la référence de requête."""
    erreurs = erreurs_validation(exc.errors())
    logger.warning(
        "Requête invalide (422) %s %s [%s] : %s",
        request.method,
        request.url.path,
        getattr(request.state, "request_id", "—"),
        champs_en_erreur(erreurs),
    )
    return JSONResponse(status_code=422, content={"detail": erreurs})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Capture les exceptions non gérées et retourne un 500 propre.
    En développement le message réel est exposé pour faciliter le débogage.
    En production on masque le détail pour ne pas exposer l'internals.
    """
    # APP-13 : type, code SQL et emplacement seulement — le message d'une
    # exception SQL peut citer les valeurs d'une ligne (nom, téléphone…).
    logger.error(
        "Erreur non gérée sur %s %s [%s] : %s | %s",
        request.method,
        request.url.path,
        getattr(request.state, "request_id", "—"),
        resume_erreur(exc),
        pile(exc),
    )
    detail = (
        f"{type(exc).__name__}: {exc}"
        if settings.is_development
        else "Une erreur interne est survenue. Veuillez réessayer."
    )
    reference = getattr(request.state, "request_id", None)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": detail, "reference": reference},
        headers={"X-Request-ID": reference} if reference else None,
    )


# ── Routes ─────────────────────────────────────────────────────────────────────

app.include_router(api_router)


# ── Métriques Prometheus ────────────────────────────────────────────────────────
# Expose /metrics (latence, taux d'erreur, requêtes/s par endpoint). Jamais
# public : Caddy bloque explicitement ce chemin (voir Caddyfile.api) — seul
# Prometheus, sur le réseau Docker interne, y accède directement (backend:8000).
from prometheus_fastapi_instrumentator import Instrumentator  # noqa: E402

Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)


@app.get("/api/v1/app-version", tags=["Health"])
@limiter.exempt
async def app_version_policy() -> dict:
    """Politique de version mobile (publique, sans authentification) — APP-46."""
    return {
        "min_version": settings.MIN_APP_VERSION or None,
        "latest_version": settings.LATEST_APP_VERSION or None,
    }


@app.get("/health", tags=["Health"], include_in_schema=False)
@app.get("/health/live", tags=["Health"], include_in_schema=False)
@limiter.exempt
async def health() -> dict:
    """Disponibilité : le processus répond (aucune dépendance contrôlée)."""
    return {"status": "ok", "app": settings.APP_NAME, "env": settings.APP_ENV}


@app.get("/health/ready", tags=["Health"], include_in_schema=False)
@limiter.exempt
async def health_ready() -> JSONResponse:
    """Préparation (APP-45) : base et migrations, Redis, stockage des médias.
    503 si une dépendance nécessaire manque ; aucun secret dans la réponse."""
    from app.core.health import preparation

    pret, corps = await preparation()
    return JSONResponse(status_code=200 if pret else 503, content=corps)
