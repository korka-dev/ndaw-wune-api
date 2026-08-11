FROM python:3.12-slim AS base

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

# Passer à l'utilisateur non-root
USER appuser

EXPOSE 8000

# ── Démarrage ─────────────────────────────────────────────────────────────────
# 1. Applique les migrations Alembic
# 2. Lance gunicorn avec uvicorn workers
#    Nombre de workers par défaut : 9 (2×vCPU+1, calibré pour un VPS 4 vCPU/8 Go —
#    voir backend/loadtest/README.md pour la méthode de calcul et le lien avec
#    MAX_CONNECTIONS_POOL/MAX_OVERFLOW et max_connections Postgres).
CMD ["sh", "-c", \
    "alembic upgrade head && \
     python scripts/create_admin.py && \
     gunicorn app.main:app \
       --worker-class uvicorn.workers.UvicornWorker \
       --workers ${GUNICORN_WORKERS:-9} \
       --max-requests ${GUNICORN_MAX_REQUESTS:-500} \
       --max-requests-jitter ${GUNICORN_MAX_REQUESTS_JITTER:-50} \
       --bind 0.0.0.0:8000 \
       --timeout 120 \
       --access-logfile - \
       --error-logfile -"]

# ── Healthcheck Docker ────────────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1
