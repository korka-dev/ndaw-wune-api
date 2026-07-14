from fastapi import APIRouter

from app.api.routes import auth
from app.api.routes.admin import (
    sessions, teachers, schools, planning, rapports, users,
    superviseurs, evaluateurs, eleves,
    suivi_seances, suivi_superviseurs, suivi_plannings, suivi_evaluations,
    rapports_journalier as admin_rapports_journalier,
    ressources, classes, rapport_questions, evaluation_competences,
    audit_logs, dashboard_stats, evaluation_sujets, evaluation_docs,
    usage_logs as admin_usage_logs,
    remarques as admin_remarques,
)
from app.api.routes.app import (
    sync, seances, rapports as app_rapports,
    rapports_journalier as app_rapports_journalier,
    supervisor_sync,
    supervisor_evaluations,
    supervisor_evaluation_sujets,
    supervisor_presences,
    supervisor_difficultes,
    teacher_evaluations,
    ressources as app_ressources,
    evaluation_docs as app_evaluation_docs,
    usage as app_usage,
    remarques as app_remarques,
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
api_router.include_router(suivi_evaluations.router,        prefix="/admin")
api_router.include_router(suivi_plannings.router,          prefix="/admin")
api_router.include_router(ressources.router,               prefix="/admin")
api_router.include_router(rapport_questions.router,         prefix="/admin")
api_router.include_router(evaluation_competences.router,    prefix="/admin")
api_router.include_router(audit_logs.router,                prefix="/admin")
api_router.include_router(dashboard_stats.router,           prefix="/admin")  # GET /admin/dashboard/stats
api_router.include_router(evaluation_sujets.router,         prefix="/admin")
api_router.include_router(evaluation_docs.router,           prefix="/admin")
api_router.include_router(admin_usage_logs.router,           prefix="/admin")  # GET /admin/usage-logs
api_router.include_router(admin_remarques.router,            prefix="/admin")  # GET /admin/remarques

# ── App mobile ────────────────────────────────────────────────────────────────
api_router.include_router(sync.router,                     prefix="/app")
api_router.include_router(seances.router,                  prefix="/app")
api_router.include_router(app_rapports.router,             prefix="/app")
api_router.include_router(app_rapports_journalier.router,  prefix="/app")
api_router.include_router(supervisor_sync.router,          prefix="/app")  # GET /app/supervisor/sync
api_router.include_router(supervisor_evaluations.router,   prefix="/app")  # /app/supervisor/eleves + /app/supervisor/evaluations
api_router.include_router(supervisor_evaluation_sujets.router, prefix="/app")  # /app/supervisor/evaluation-sujets
api_router.include_router(supervisor_presences.router,     prefix="/app")  # /app/supervisor/presences
api_router.include_router(supervisor_difficultes.router,   prefix="/app")  # /app/supervisor/difficultes
api_router.include_router(teacher_evaluations.router,      prefix="/app")  # /app/teacher/evaluations
api_router.include_router(app_ressources.router,           prefix="/app")
api_router.include_router(app_evaluation_docs.router,      prefix="/app")  # /app/supervisor/evaluation-docs
api_router.include_router(app_usage.router,                 prefix="/app")  # POST /app/usage
api_router.include_router(app_remarques.router,             prefix="/app")  # /app/remarques
