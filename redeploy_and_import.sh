#!/usr/bin/env bash
# ==============================================================================
#  NDAW WUNE — Script de redéploiement autonome Backend + Import (VPS)
#
#  Usage (à lancer après votre "git pull" manuel dans le dossier backend du VPS) :
#    • Redéployer seulement :
#        ./redeploy_and_import.sh
#    • Redéployer + Importer un fichier Excel :
#        ./redeploy_and_import.sh --import ~/LISTES_ELEVES.xlsx
#    • Redéployer + Simuler l'import (dry-run) :
#        ./redeploy_and_import.sh --import ~/LISTES_ELEVES.xlsx --dry-run
# ==============================================================================

set -euo pipefail

# ── Couleurs pour l'affichage ──────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

log()     { echo -e "${CYAN}▶  $*${NC}"; }
success() { echo -e "${GREEN}✅  $*${NC}"; }
warn()    { echo -e "${YELLOW}⚠️   $*${NC}"; }
error()   { echo -e "${RED}❌  $*${NC}"; exit 1; }
header()  { echo -e "\n${BOLD}${CYAN}══════════════════════════════════════════${NC}"; echo -e "${BOLD}   $*${NC}"; echo -e "${BOLD}${CYAN}══════════════════════════════════════════${NC}"; }

BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$BACKEND_DIR"

EXCEL_FILE=""
DRY_RUN=false

# ── Parsing des arguments ─────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --import)
      if [[ -z "${2:-}" ]]; then error "Argument manquant pour --import"; fi
      EXCEL_FILE="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    *)
      error "Argument inconnu : $1\nUsage : $0 [--import fichier.xlsx] [--dry-run]"
      ;;
  esac
done

# ── Vérifications ─────────────────────────────────────────────────────────────
header "NDAW WUNE — Redéploiement Backend VPS"
[[ -f "docker-compose.yml" ]] || error "docker-compose.yml introuvable dans $(pwd)"
[[ -f ".env" ]]               || error "Fichier .env introuvable dans $(pwd). Copiez .env.example."
command -v docker >/dev/null  || error "Docker n'est pas installé sur le VPS."

# ── 1. Rebuild & Relance ──────────────────────────────────────────────────────
header "Étape 1/3 — Reconstruction et démarrage de l'API"
log "Arrêt propre du conteneur backend..."
docker compose down

log "Reconstruction de l'image (sans cache) et relance des conteneurs..."
docker compose build --no-cache
docker compose up -d
success "Services Docker opérationnels."

# ── 2. Attente Healthcheck et migrations ──────────────────────────────────────
header "Étape 2/3 — Attente du démarrage de l'API"
log "En attente que le service backend réponde..."
MAX_WAIT=120
WAITED=0
until docker compose exec -T backend curl -sf http://localhost:8000/health > /dev/null 2>&1; do
  if [[ $WAITED -ge $MAX_WAIT ]]; then
    warn "Timeout atteint (${MAX_WAIT}s). Logs de l'API :"
    docker compose logs --tail=40 backend
    error "L'API backend n'a pas répondu dans le temps imparti."
  fi
  echo -n "."
  sleep 3
  WAITED=$((WAITED + 3))
done
echo ""
success "L'API backend répond parfaitement !"

log "Application automatique des nouvelles migrations Alembic..."
docker compose exec -T backend alembic upgrade head
success "Base de données migrée à jour."

# ── 3. Importation des élèves (si demandée) ──────────────────────────────────
if [[ -z "$EXCEL_FILE" ]]; then
  header "Déploiement Terminé !"
  success "L'API Backend a été redéployée avec succès."
  echo -e "\n💡 ${YELLOW}Note : Aucun import n'a été effectué.${NC}"
  echo -e "   Pour importer vos élèves plus tard, lancez :"
  echo -e "   ${CYAN}./redeploy_and_import.sh --import <chemin_fichier_excel.xlsx>${NC}\n"
  exit 0
fi

header "Étape 3/3 — Import des élèves"
# Vérifier la présence du fichier Excel sur le host VPS
if [[ ! -f "$EXCEL_FILE" ]]; then
  error "Le fichier Excel spécifié est introuvable sur le VPS : $EXCEL_FILE"
fi

# Récupérer l'ID du container backend
CONTAINER_ID=$(docker compose ps -q backend)
[[ -n "$CONTAINER_ID" ]] || error "Conteneur backend introuvable."

# Vérifier que import_eleves.py existe localement
IMPORT_SCRIPT="import_eleves.py"
[[ -f "$IMPORT_SCRIPT" ]] || error "Le script d'import '$IMPORT_SCRIPT' est introuvable."

EXCEL_BASENAME=$(basename "$EXCEL_FILE")
CONTAINER_EXCEL="/tmp/${EXCEL_BASENAME}"
CONTAINER_SCRIPT="/tmp/import_eleves.py"

log "Copie des fichiers dans le conteneur..."
docker cp "$EXCEL_FILE"    "${CONTAINER_ID}:${CONTAINER_EXCEL}"
docker cp "$IMPORT_SCRIPT" "${CONTAINER_ID}:${CONTAINER_SCRIPT}"

log "Vérification des dépendances Python requises..."
docker compose exec -T backend pip install --quiet pandas openpyxl psycopg2-binary python-dotenv

DRY_FLAG=""
if $DRY_RUN; then
  DRY_FLAG="--dry-run"
  warn "Lancement de l'import en mode SIMULATION (dry-run)..."
else
  log "Lancement de l'import réel des élèves dans la base PostgreSQL..."
fi

docker compose exec -T backend python3 "$CONTAINER_SCRIPT" \
  --file "$CONTAINER_EXCEL" \
  --env /app/.env \
  --docker \
  $DRY_FLAG

log "Nettoyage des fichiers temporaires dans le conteneur..."
docker compose exec -T backend rm -f "$CONTAINER_EXCEL" "$CONTAINER_SCRIPT"

header "Déploiement & Import Terminés !"
if $DRY_RUN; then
  success "La simulation s'est déroulée avec succès. Aucune donnée n'a été modifiée."
else
  success "Le backend a été redéployé et les élèves ont été importés avec succès !"
fi
echo ""
