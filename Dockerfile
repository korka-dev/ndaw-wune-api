FROM python:3.12-slim AS base

# Commit livré (APP-42/45) : affiché par /health/ready pour contrôler une
# bascule ou un retour arrière. Passé par docker compose (APP_COMMIT).
ARG APP_COMMIT=inconnu
ENV APP_COMMIT=${APP_COMMIT}

# Variables d'environnement Python
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ── Dépendances système minimales ─────────────────────────────────────────────
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

# ── Utilisateur non-root (bonne pratique de sécurité) ────────────────────────
RUN groupadd --gid 1001 appgroup \
    && useradd --uid 1001 --gid appgroup --shell /bin/bash --create-home appuser

WORKDIR /app

# ── Installation des dépendances (layer mis en cache si requirements.txt inchangé) ──
COPY --chown=appuser:appgroup requirements.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

# ── Code source ───────────────────────────────────────────────────────────────
COPY --chown=appuser:appgroup . .

# Dossier des fichiers uploadés : doit exister dans l'image et appartenir à
# appuser, sinon le volume Docker "uploads_data" est créé en root au premier
# démarrage et l'API ne peut plus y écrire.
RUN mkdir -p /app/uploads && chown appuser:appgroup /app/uploads

# Passer à l'utilisateur non-root
USER appuser

EXPOSE 8000

# ── Démarrage ─────────────────────────────────────────────────────────────────
# 1. Applique les migrations Alembic
#    (le compte admin initial n'est PLUS créé au démarrage — APP-03 :
#     commande ponctuelle `make create-admin` ou deploy_vps.sh au 1er install)
# 2. Lance gunicorn avec uvicorn workers
#    Nombre de workers par défaut : 9 (2×vCPU+1, calibré pour un VPS 4 vCPU/8 Go —
#    voir backend/loadtest/README.md pour la méthode de calcul et le lien avec
#    MAX_CONNECTIONS_POOL/MAX_OVERFLOW et max_connections Postgres).
CMD ["sh", "-c", \
    "alembic upgrade head && \
     gunicorn app.main:app \
       --worker-class uvicorn.workers.UvicornWorker \
       --workers ${GUNICORN_WORKERS:-9} \
       --max-requests ${GUNICORN_MAX_REQUESTS:-500} \
       --max-requests-jitter ${GUNICORN_MAX_REQUESTS_JITTER:-50} \
       --bind 0.0.0.0:8000 \
       --forwarded-allow-ips '*' \
       --timeout 120 \
       --access-logfile - \
       --error-logfile -"]

# ── Healthcheck Docker ────────────────────────────────────────────────────────
# Préparation (APP-45) : base à jour des migrations, Redis et médias accessibles.
# Le conteneur passe « unhealthy » (visible, alertable) sans redémarrer en boucle.
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -fsS http://localhost:8000/health/ready >/dev/null || exit 1
