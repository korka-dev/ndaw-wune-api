from fastapi import APIRouter, Depends
from app.core.deps import sync_invalidation_on_admin_write

from app.api.routes import auth, support
from app.api.routes.admin import (
    sessions, teachers, schools, planning, rapports, users,
    superviseurs, evaluateurs, eleves,
    suivi_seances, suivi_superviseurs, suivi_plannings, suivi_evaluations,
    rapports_journalier as admin_rapports_journalier,
    ressources, classes, rapport_questions, rapport_difficultes, rapport_libelles, evaluation_competences,
    audit_logs, dashboard_stats, evaluation_sujets, evaluation_docs,
    usage_logs as admin_usage_logs,
    remarques as admin_remarques,
    rapport_difficulte_resolutions, progression_configs,
    eleve_remplacements as admin_eleve_remplacements,
    import_export as admin_import_export,
    comptes_terrain as admin_comptes_terrain,
    analytique as admin_analytique,
    visites as admin_visites,
    demandes_appui as admin_demandes_appui,
    campagnes as admin_campagnes,
    tableau_bord as admin_tableau_bord,
    recherche as admin_recherche,
    cles_service as admin_cles_service,
    reprise as admin_reprise,
    terrain as admin_terrain,
)
from app.api.routes.app import (
    sync, seances, rapports as app_rapports,
    rapports_journalier as app_rapports_journalier,
    supervisor_sync,
    supervisor_evaluations,
    supervisor_evaluation_sujets,
    supervisor_presences,
    supervisor_difficultes,
    supervisor_progression,
    teacher_evaluations,
    ressources as app_ressources,
    evaluation_docs as app_evaluation_docs,
    usage as app_usage,
    remarques as app_remarques,
    remplacements as app_remplacements,
    remplacants as app_remplacants,
    supervisor_visites,
    supervisor_terrain,
    terrain as app_terrain,
)
from app.api.routes import service as service_routes

api_router = APIRouter(prefix="/api/v1")

# ── Auth ──────────────────────────────────────────────────────────────────────
_ADMIN_DEPS = [Depends(sync_invalidation_on_admin_write)]

api_router.include_router(auth.router)
api_router.include_router(support.router)

# ── Admin ─────────────────────────────────────────────────────────────────────
api_router.include_router(users.router,                    prefix="/admin", dependencies=_ADMIN_DEPS)
api_router.include_router(sessions.router,                 prefix="/admin", dependencies=_ADMIN_DEPS)
api_router.include_router(teachers.router,                 prefix="/admin", dependencies=_ADMIN_DEPS)
api_router.include_router(schools.router,                  prefix="/admin", dependencies=_ADMIN_DEPS)
api_router.include_router(planning.router,                 prefix="/admin", dependencies=_ADMIN_DEPS)
api_router.include_router(admin_rapports_journalier.router, prefix="/admin", dependencies=_ADMIN_DEPS)  # avant rapports pour éviter conflit /{rapport_id}
api_router.include_router(rapports.router,                 prefix="/admin", dependencies=_ADMIN_DEPS)
api_router.include_router(superviseurs.router,             prefix="/admin", dependencies=_ADMIN_DEPS)
api_router.include_router(evaluateurs.router,              prefix="/admin", dependencies=_ADMIN_DEPS)
api_router.include_router(eleves.router,                   prefix="/admin", dependencies=_ADMIN_DEPS)
api_router.include_router(classes.router,                  prefix="/admin", dependencies=_ADMIN_DEPS)
api_router.include_router(suivi_seances.router,            prefix="/admin", dependencies=_ADMIN_DEPS)
api_router.include_router(suivi_superviseurs.router,       prefix="/admin", dependencies=_ADMIN_DEPS)
api_router.include_router(suivi_evaluations.router,        prefix="/admin", dependencies=_ADMIN_DEPS)
api_router.include_router(suivi_plannings.router,          prefix="/admin", dependencies=_ADMIN_DEPS)
api_router.include_router(ressources.router,               prefix="/admin", dependencies=_ADMIN_DEPS)
api_router.include_router(rapport_questions.router,         prefix="/admin", dependencies=_ADMIN_DEPS)
api_router.include_router(rapport_difficultes.router,        prefix="/admin", dependencies=_ADMIN_DEPS)
api_router.include_router(rapport_libelles.router,           prefix="/admin", dependencies=_ADMIN_DEPS)
api_router.include_router(evaluation_competences.router,    prefix="/admin", dependencies=_ADMIN_DEPS)
api_router.include_router(audit_logs.router,                prefix="/admin", dependencies=_ADMIN_DEPS)
api_router.include_router(dashboard_stats.router,           prefix="/admin", dependencies=_ADMIN_DEPS)  # GET /admin/dashboard/stats
api_router.include_router(evaluation_sujets.router,         prefix="/admin", dependencies=_ADMIN_DEPS)
api_router.include_router(evaluation_docs.router,           prefix="/admin", dependencies=_ADMIN_DEPS)
api_router.include_router(admin_usage_logs.router,           prefix="/admin", dependencies=_ADMIN_DEPS)  # GET /admin/usage-logs
api_router.include_router(admin_remarques.router,            prefix="/admin", dependencies=_ADMIN_DEPS)  # GET /admin/remarques
api_router.include_router(rapport_difficulte_resolutions.router, prefix="/admin", dependencies=_ADMIN_DEPS)  # PATCH /admin/rapport-difficulte-resolutions/{id}/resolve
api_router.include_router(progression_configs.router,        prefix="/admin", dependencies=_ADMIN_DEPS)  # /admin/progression-configs
api_router.include_router(admin_eleve_remplacements.router,  prefix="/admin", dependencies=_ADMIN_DEPS)  # GET /admin/remplacements
api_router.include_router(admin_import_export.router,         prefix="/admin", dependencies=_ADMIN_DEPS)  # GET /admin/import-export/export · POST /admin/import-export/import
api_router.include_router(admin_comptes_terrain.router,       prefix="/admin", dependencies=_ADMIN_DEPS)  # /admin/comptes-terrain — tuteurs & superviseurs, réinitialisation mdp
api_router.include_router(admin_analytique.router,            prefix="/admin", dependencies=_ADMIN_DEPS)  # /admin/analytique — indicateurs, pack CSV, API analytique (lot L4)

# Lot L6 (rapport V2, Partie II D8-D10, APP-33)
for _r in (admin_visites, admin_demandes_appui, admin_campagnes, admin_tableau_bord,
           admin_recherche, admin_cles_service, admin_reprise, admin_terrain):
    api_router.include_router(_r.router, prefix="/admin", dependencies=_ADMIN_DEPS)

# ── Accès de service (clé d'API, lecture seule, pseudonymisé) ─────────────────
api_router.include_router(service_routes.router)

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
api_router.include_router(supervisor_progression.router,   prefix="/app")  # /app/supervisor/progression
api_router.include_router(teacher_evaluations.router,      prefix="/app")  # /app/teacher/evaluations
api_router.include_router(app_ressources.router,           prefix="/app")
api_router.include_router(app_evaluation_docs.router,      prefix="/app")  # /app/supervisor/evaluation-docs
api_router.include_router(app_usage.router,                 prefix="/app")  # POST /app/usage
api_router.include_router(app_remarques.router,             prefix="/app")  # /app/remarques
api_router.include_router(app_remplacements.router,         prefix="/app")  # POST /app/remplacements
api_router.include_router(app_remplacants.router,           prefix="/app")  # POST /app/remplacants
api_router.include_router(supervisor_visites.router,        prefix="/app")  # /app/supervisor/visites · grilles-visite · demandes-appui (L6)
# Lot L7 (retours des tuteurs et superviseurs)
api_router.include_router(supervisor_terrain.router,        prefix="/app")  # /app/supervisor/remplacements · rapports-tuteurs · tuteurs-activite · messages
api_router.include_router(app_terrain.router,               prefix="/app")  # /app/propositions-groupe · /app/mes-resultats
