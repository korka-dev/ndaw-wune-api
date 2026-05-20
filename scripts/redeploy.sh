#!/usr/bin/env bash
# ==============================================================================
#  NDAW WUNE — Script de redéploiement (appelé par GitHub Actions ou en manuel)
#
#  Usage manuel sur le VPS :
#    cd /opt/ndawwune          # ou le répertoire où se trouve le projet
#    bash backend/scripts/redeploy.sh
#
#  Ce script suppose que :
#    - Docker et Docker Compose sont installés
#    - Le fichier backend/.env existe et est configuré
#    - Le répertoire courant est la racine du projet ($VPS_APP_DIR)
#    - Git est configuré (accès au dépôt)
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

# ── Répertoire du projet ──────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_DIR="$(cd "$BACKEND_DIR/.." && pwd)"

cd "$PROJECT_DIR"

echo ""
echo -e "${BOLD}══════════════════════════════════════════════${NC}"
echo -e "${BOLD}   NDAW WUNE — Redéploiement Backend VPS      ${NC}"
echo -e "${BOLD}══════════════════════════════════════════════${NC}"
echo ""

# ── Pré-requis ────────────────────────────────────────────────────────────────
log "[0/6] Vérification des pré-requis..."

if ! command -v docker &>/dev/null; then
  err "Docker n'est pas installé. Lancez d'abord : bash backend/deploy_vps.sh"
  exit 1
fi

if [ ! -f "$BACKEND_DIR/.env" ]; then
  err "Le fichier backend/.env est manquant !"
  echo "     Copiez backend/.env.example → backend/.env et remplissez les valeurs."
  exit 1
fi

ok "Pré-requis vérifiés."
echo ""

# ── 1. Récupérer la dernière version du code ──────────────────────────────────
log "[1/6] Récupération du code (git pull)..."

git fetch --all
git pull origin main

ok "Code mis à jour."
echo ""

# ── 2. Build de l'image backend ──────────────────────────────────────────────
log "[2/6] Build de l'image Docker (backend uniquement)..."

cd "$BACKEND_DIR"
docker compose build backend

ok "Image buildée."
echo ""

# ── 3. S'assurer que db et redis tournent ────────────────────────────────────
log "[3/6] Démarrage de db et redis (si pas déjà actifs)..."

docker compose up -d db redis

# Attendre que postgres soit prêt avant de lancer les migrations
log "    Attente de PostgreSQL..."
MAX_PG=15
for i in $(seq 1 $MAX_PG); do
  if docker compose exec -T db pg_isready -U "${POSTGRES_USER:-ndawwune}" &>/dev/null; then
    break
  fi
  echo "   PostgreSQL pas encore prêt ($i/$MAX_PG)..."
  sleep 3
done

ok "db et redis actifs."
echo ""

# ── 4. Migrations Alembic ─────────────────────────────────────────────────────
log "[4/6] Application des migrations Alembic..."

# On lance alembic dans le conteneur backend (image déjà buildée)
docker compose run --rm --no-deps backend alembic upgrade head

ok "Migrations appliquées."
echo ""

# ── 5. Redémarrage du backend ────────────────────────────────────────────────
log "[5/6] Redémarrage du service backend..."

docker compose up -d --no-deps backend

ok "Conteneur backend redémarré."
echo ""

# ── 6. Health check ───────────────────────────────────────────────────────────
log "[6/6] Attente du healthcheck Docker (max 2 min)..."

BACKEND_CONTAINER=$(docker compose ps -q backend 2>/dev/null)
MAX_TRIES=20
STATUS=""

for i in $(seq 1 $MAX_TRIES); do
  STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$BACKEND_CONTAINER" 2>/dev/null || echo "unknown")

  case "$STATUS" in
    healthy)
      break
      ;;
    unhealthy)
      err "Le backend est en état 'unhealthy' après le démarrage."
      echo ""
      echo "── Derniers logs du backend ──────────────────────"
      docker compose logs --tail=50 backend
      exit 1
      ;;
    *)
      echo "   Essai $i/$MAX_TRIES — statut : ${STATUS:-inconnu}..."
      sleep 6
      ;;
  esac
done

if [ "$STATUS" != "healthy" ]; then
  err "Timeout : le backend n'est pas devenu 'healthy' en temps imparti."
  echo ""
  echo "── Derniers logs du backend ──────────────────────"
  docker compose logs --tail=50 backend
  exit 1
fi

ok "Backend healthy !"
echo ""

# ── Nettoyage des images orphelines ──────────────────────────────────────────
log "Nettoyage des anciennes images Docker..."
docker image prune -f 2>/dev/null || true
ok "Nettoyage effectué."
echo ""

# ── Résumé ────────────────────────────────────────────────────────────────────
echo -e "${BOLD}${GREEN}══════════════════════════════════════════════${NC}"
echo -e "${BOLD}${GREEN}   ✅ Déploiement terminé avec succès !        ${NC}"
echo -e "${BOLD}${GREEN}══════════════════════════════════════════════${NC}"
echo ""
echo -e "${BOLD}Services actifs :${NC}"
docker compose ps
echo ""
echo -e "${BOLD}Endpoint de santé :${NC}"
curl -sf https://api.ndawwune.cloud/health && echo "" || warn "Health check HTTP non accessible (normal si Nginx n'est pas encore configuré)"
echo ""
