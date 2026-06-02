from fastapi import APIRouter

from app.api.routes import auth
from app.api.routes.admin import (
    sessions, teachers, schools, planning, rapports, users,
    superviseurs, evaluateurs, eleves,
    suivi_seances, suivi_superviseurs, suivi_plannings,
    rapports_journalier as admin_rapports_journalier,
    ressources, classes,
)
from app.api.routes.app import (
    sync, seances, rapports as app_rapports,
    rapports_journalier as app_rapports_journalier,
    supervisor_sync,
    ressources as app_ressources,
)

api_router = APIRouter(prefix="/api/v1")

# ── Auth ──────────────────────────────────────────────────────────────────────
api_router.include_router(auth.router)

# ── Admin ─────────────────────────────────────────────────────────────────────
api_router.include_router(users.router,                    prefix="/admin")
api_router.include_router(sessions.router,                 prefix="/admin")
api_router.include_router(teachers.router,                 prefix="/admin")
api_router.include_router(schools.router,                  prefix="/admin")
api_router.include_router(planning.router,                 prefix="/admin")
api_router.include_router(admin_rapports_journalier.router, prefix="/admin")  # avant rapports pour éviter conflit /{rapport_id}
api_router.include_router(rapports.router,                 prefix="/admin")
api_router.include_router(superviseurs.router,             prefix="/admin")
api_router.include_router(evaluateurs.router,              prefix="/admin")
api_router.include_router(eleves.router,                   prefix="/admin")
api_router.include_router(classes.router,                  prefix="/admin")
api_router.include_router(suivi_seances.router,            prefix="/admin")
api_router.include_router(suivi_superviseurs.router,       prefix="/admin")
api_router.include_router(suivi_plannings.router,          prefix="/admin")
api_router.include_router(ressources.router,               prefix="/admin")

# ── App mobile ────────────────────────────────────────────────────────────────
api_router.include_router(sync.router,                     prefix="/app")
api_router.include_router(seances.router,                  prefix="/app")
api_router.include_router(app_rapports.router,             prefix="/app")
api_router.include_router(app_rapports_journalier.router,  prefix="/app")
api_router.include_router(supervisor_sync.router,          prefix="/app")  # GET /app/supervisor/sync
api_router.include_router(app_ressources.router,           prefix="/app")
