#!/usr/bin/env bash
# ==============================================================================
#  NDAW WUNE — Redéploiement du backend FastAPI sur VPS
#
#  Usage (depuis le dossier backend/ sur le VPS) :
#    chmod +x redeploy_backend.sh && ./redeploy_backend.sh
#
#  Ce script :
#    1. Récupère le code le plus récent (git pull)
#    2. Rebuild uniquement le conteneur backend (DB et Redis conservés)
#    3. Applique les migrations Alembic automatiquement au démarrage
# ==============================================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

log()     { echo -e "${CYAN}▶  $*${NC}"; }
success() { echo -e "${GREEN}✅ $*${NC}"; }
warn()    { echo -e "${YELLOW}⚠️  $*${NC}"; }
err()     { echo -e "${RED}❌ $*${NC}"; exit 1; }
header()  { echo -e "\n${BOLD}${CYAN}══════════════════════════════════════════${NC}"; echo -e "${BOLD}   $*${NC}"; echo -e "${BOLD}${CYAN}══════════════════════════════════════════${NC}"; }

BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$BACKEND_DIR"

header "NDAW WUNE — Redéploiement Backend"

# ── Prérequis ─────────────────────────────────────────────────────────────────
command -v docker &>/dev/null || err "Docker n'est pas installé."
docker compose version &>/dev/null || err "Docker Compose plugin manquant."
[ -f ".env" ] || err ".env introuvable. Créez-le : cp .env.example .env && nano .env"

# ── Git pull ──────────────────────────────────────────────────────────────────
header "Étape 1/3 — Mise à jour du code"

if git rev-parse --git-dir &>/dev/null; then
  BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "main")
  log "Branche : $BRANCH — récupération..."
  git fetch origin "$BRANCH"
  LOCAL=$(git rev-parse HEAD)
  REMOTE=$(git rev-parse "origin/$BRANCH")
  if [ "$LOCAL" = "$REMOTE" ]; then
    success "Code déjà à jour ($(git log -1 --format='%h %s'))."
  else
    git reset --hard "origin/$BRANCH"
    success "Code mis à jour : $(git log -1 --format='%h %s')"
  fi
else
  warn "Pas de dépôt git — code actuel utilisé."
fi

# ── Rebuild backend (DB et Redis conservés) ───────────────────────────────────
header "Étape 2/3 — Rebuild du backend"
log "Reconstruction de l'image (DB et Redis restent en vie)..."
docker compose build --no-cache backend
success "Image backend reconstruite."

# ── Redémarrage ───────────────────────────────────────────────────────────────
header "Étape 3/3 — Redémarrage"
docker compose up -d backend

log "Attente du démarrage (migrations Alembic incluses)..."
for i in $(seq 1 20); do
  STATUS=$(docker inspect --format='{{.State.Health.Status}}' \
    "$(docker compose ps -q backend 2>/dev/null)" 2>/dev/null || echo "starting")
  if [ "$STATUS" = "healthy" ]; then
    success "Backend en ligne !"
    break
  fi
  echo "  ($i/20) statut : ${STATUS:-démarrage…}"
  sleep 6
done

echo ""
docker compose ps
echo ""
success "Redéploiement backend terminé."
echo "  Logs : docker compose logs -f backend"
echo "  Test : curl http://localhost:8000/health"
