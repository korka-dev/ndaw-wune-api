#!/usr/bin/env bash
# ==============================================================================
#  NDAW WUNE — Script de redéploiement VPS
#
#  Repo  : git@github.com:korka-dev/ndaw-wune-api.git
#  VPS   : ndaw-wune  (nom du projet : ndaw-wune)
#
#  Usage manuel sur le VPS :
#    cd ndaw-wune
#    bash backend/scripts/redeploy.sh
#
#  Pré-requis :
#    - Docker + Docker Compose installés
#    - backend/.env configuré
#    - Clé SSH du VPS autorisée sur GitHub (git@github.com)
# ==============================================================================

set -euo pipefail

# ── Couleurs ──────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;36m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

log()  { echo -e "${BLUE}▶ $*${NC}"; }
ok()   { echo -e "${GREEN}✅ $*${NC}"; }
warn() { echo -e "${YELLOW}⚠  $*${NC}"; }
err()  { echo -e "${RED}❌ $*${NC}"; }

# ── Répertoires ───────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_DIR="ndaw-wune"

# Si on n'est pas dans ndaw-wune, on s'y place
if [ -d "$PROJECT_DIR" ]; then
  cd "$PROJECT_DIR"
else
  # Fallback : racine du projet détectée depuis le script
  cd "$BACKEND_DIR/.."
  warn "Répertoire ndaw-wune introuvable, utilisation de $(pwd)"
fi

echo ""
echo -e "${BOLD}══════════════════════════════════════════════${NC}"
echo -e "${BOLD}   NDAW WUNE — Redéploiement Backend VPS      ${NC}"
echo -e "${BOLD}══════════════════════════════════════════════${NC}"
echo -e "   Repo : git@github.com:korka-dev/ndaw-wune-api.git"
echo -e "   Dir  : $(pwd)"
echo ""

# ── Pré-requis ────────────────────────────────────────────────────────────────
log "[0/6] Vérification des pré-requis..."

if ! command -v docker &>/dev/null; then
  err "Docker n'est pas installé."
  exit 1
fi

if [ ! -f "backend/.env" ]; then
  err "Le fichier backend/.env est manquant !"
  echo "     Créez-le depuis backend/.env.example et remplissez les valeurs."
  exit 1
fi

ok "Pré-requis vérifiés."
echo ""

# ── 1. Récupération du code ───────────────────────────────────────────────────
log "[1/6] Récupération du code depuis GitHub..."

git fetch origin
git pull origin main

ok "Code à jour — $(git log -1 --format='%h %s' HEAD)"
echo ""

# ── 2. Build de l'image backend ──────────────────────────────────────────────
log "[2/6] Build de l'image Docker (backend)..."

cd backend
docker compose build backend
cd ..

ok "Image buildée."
echo ""

# ── 3. Démarrage de db et redis ───────────────────────────────────────────────
log "[3/6] Démarrage de db et redis..."

cd backend
docker compose up -d db redis

# Attendre que PostgreSQL soit prêt
log "    Attente de PostgreSQL..."
for i in $(seq 1 15); do
  if docker compose exec -T db pg_isready &>/dev/null; then
    break
  fi
  echo "   PostgreSQL pas encore prêt ($i/15)..."
  sleep 3
done

ok "db et redis actifs."
echo ""

# ── 4. Migrations Alembic ─────────────────────────────────────────────────────
log "[4/6] Application des migrations Alembic (alembic upgrade head)..."

docker compose run --rm --no-deps backend alembic upgrade head

ok "Migrations appliquées."
echo ""

# ── 5. Redémarrage du backend ────────────────────────────────────────────────
log "[5/6] Redémarrage du service backend..."

docker compose up -d --no-deps backend

ok "Conteneur backend redémarré."
echo ""

# ── 6. Health check ───────────────────────────────────────────────────────────
log "[6/6] Health check (max 2 min)..."

BACKEND_CONTAINER=$(docker compose ps -q backend 2>/dev/null)
MAX_TRIES=20
STATUS=""

for i in $(seq 1 $MAX_TRIES); do
  STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$BACKEND_CONTAINER" 2>/dev/null || echo "unknown")
  case "$STATUS" in
    healthy)   break ;;
    unhealthy)
      err "Backend 'unhealthy'. Logs :"
      docker compose logs --tail=50 backend
      cd ..
      exit 1
      ;;
    *)
      echo "   Essai $i/$MAX_TRIES — statut : ${STATUS:-inconnu}..."
      sleep 6
      ;;
  esac
done

if [ "$STATUS" != "healthy" ]; then
  err "Timeout : le backend n'est pas devenu healthy."
  docker compose logs --tail=50 backend
  cd ..
  exit 1
fi

ok "Backend healthy !"
echo ""

# ── Nettoyage ─────────────────────────────────────────────────────────────────
log "Nettoyage des anciennes images..."
docker image prune -f 2>/dev/null || true

cd ..

# ── Résumé ────────────────────────────────────────────────────────────────────
echo -e "${BOLD}${GREEN}══════════════════════════════════════════════${NC}"
echo -e "${BOLD}${GREEN}   ✅ Déploiement terminé avec succès !        ${NC}"
echo -e "${BOLD}${GREEN}══════════════════════════════════════════════${NC}"
echo ""
echo -e "${BOLD}Services actifs :${NC}"
cd backend && docker compose ps && cd ..
echo ""
echo -e "${BOLD}Vérification de l'API :${NC}"
curl -sf https://api.ndawwune.cloud/health && echo "" \
  || warn "API non accessible via HTTPS (vérifier Nginx / certificat SSL)"
echo ""
