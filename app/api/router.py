from fastapi import APIRouter

from app.api.routes import auth
from app.api.routes.admin import sessions, teachers, schools, planning, rapports, users
from app.api.routes.app import sync, seances, rapports as app_rapports

api_router = APIRouter(prefix="/api/v1")

# ── Auth ──────────────────────────────────────────────────────────────────────
api_router.include_router(auth.router)

# ── Admin ─────────────────────────────────────────────────────────────────────
api_router.include_router(users.router,    prefix="/admin")
api_router.include_router(sessions.router, prefix="/admin")
api_router.include_router(teachers.router, prefix="/admin")
api_router.include_router(schools.router,  prefix="/admin")
api_router.include_router(planning.router, prefix="/admin")
api_router.include_router(rapports.router, prefix="/admin")

# ── App mobile ────────────────────────────────────────────────────────────────
api_router.include_router(sync.router,         prefix="/app")
api_router.include_router(seances.router,      prefix="/app")
api_router.include_router(app_rapports.router, prefix="/app")
