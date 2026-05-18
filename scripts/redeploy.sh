#!/usr/bin/env bash
# ==============================================================================
#  NDAW WUNE — Script de redéploiement (appelé par GitHub Actions)
#
#  Usage local  : bash backend/scripts/redeploy.sh
#  Usage CI/CD  : appelé automatiquement depuis .github/workflows/deploy.yml
#
#  Ce script suppose que :
#    - Docker et Docker Compose sont installés
#    - Le fichier backend/.env existe et est configuré
#    - Le répertoire courant est la racine du projet ($VPS_APP_DIR)
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
# Ce script peut être lancé depuis n'importe où ; il se place lui-même à la
# racine du projet en remontant depuis son propre chemin.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$BACKEND_DIR"

echo ""
echo -e "${BOLD}══════════════════════════════════════════════${NC}"
echo -e "${BOLD}   NDAW WUNE — Redéploiement Backend          ${NC}"
echo -e "${BOLD}══════════════════════════════════════════════${NC}"
echo ""

# ── Pré-requis ────────────────────────────────────────────────────────────────
log "[0/4] Vérification des pré-requis..."

if ! command -v docker &>/dev/null; then
  err "Docker n'est pas installé. Lancez d'abord : bash backend/deploy_vps.sh"
  exit 1
fi

if [ ! -f ".env" ]; then
  err "Le fichier backend/.env est manquant !"
  echo "     Copiez backend/.env.example → backend/.env et remplissez les valeurs."
  exit 1
fi

ok "Pré-requis vérifiés."
echo ""

# ── 1. Build de l'image backend ──────────────────────────────────────────────
log "[1/4] Build de l'image Docker (backend uniquement)..."

docker compose build backend

ok "Image buildée."
echo ""

# ── 2. Redémarrage sans interruption (zero-downtime minimal) ─────────────────
# On redémarre uniquement le service backend, pas db/redis qui sont déjà up.
log "[2/4] Redémarrage du service backend..."

# S'assurer que db et redis tournent (sans les reconstruire)
docker compose up -d db redis

# Redémarrer backend avec la nouvelle image
docker compose up -d --no-deps backend

ok "Conteneur backend redémarré."
echo ""

# ── 3. Health check ───────────────────────────────────────────────────────────
log "[3/4] Attente du healthcheck Docker (max 2 min)..."

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
      docker compose logs --tail=40 backend
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
  docker compose logs --tail=40 backend
  exit 1
fi

ok "Backend healthy !"
echo ""

# ── 4. Nettoyage des images orphelines ───────────────────────────────────────
log "[4/4] Nettoyage des anciennes images Docker..."
docker image prune -f --filter "label=com.docker.compose.project=$(basename "$BACKEND_DIR")" 2>/dev/null || true
ok "Nettoyage effectué."
echo ""

# ── Résumé ────────────────────────────────────────────────────────────────────
echo -e "${BOLD}${GREEN}══════════════════════════════════════════════${NC}"
echo -e "${BOLD}${GREEN}   ✅ Déploiement terminé avec succès !        ${NC}"
echo -e "${BOLD}${GREEN}══════════════════════════════════════════════${NC}"
echo ""
docker compose ps
echo ""
