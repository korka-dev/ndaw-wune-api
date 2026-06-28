#!/usr/bin/env bash
# ==============================================================================
#  NDAW WUNE — Script de redéploiement autonome Backend + Import (VPS)
#
#  Usage (à lancer après votre "git pull" manuel dans le dossier backend du VPS) :
#    • Redéployer seulement :
#        ./redeploy_and_import.sh
#    • Redéployer + Importer élèves (ancien format) :
#        ./redeploy_and_import.sh --import ~/LISTES_ELEVES.xlsx
#    • Redéployer + Importer remplacement (écoles + superviseurs + classes + élèves) :
#        ./redeploy_and_import.sh --import-remplacement ~/ListeDesELevesPricipauxRemplacement.xlsx
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
REMPLACEMENT_FILE=""

# ── Parsing des arguments ─────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --import)
      if [[ -z "${2:-}" ]]; then error "Argument manquant pour --import"; fi
      EXCEL_FILE="$2"
      shift 2
      ;;
    --import-remplacement)
      if [[ -n "${2:-}" && ! "${2:-}" == --* ]]; then
        REMPLACEMENT_FILE="$2"
        shift 2
      else
        REMPLACEMENT_FILE="${BACKEND_DIR}/ListeDesELevesPricipauxRemplacement.xlsx"
        shift 1
      fi
      ;;
    *)
      error "Argument inconnu : $1\nUsage : $0 [--import fichier.xlsx] [--import-remplacement fichier.xlsx]"
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

# ── 3. Importations (si demandées) ────────────────────────────────────────────

CONTAINER_ID=$(docker compose ps -q backend)
[[ -n "$CONTAINER_ID" ]] || error "Conteneur backend introuvable."

# Fonction utilitaire pour exécuter un import dans le conteneur
run_import() {
  local FILE="$1"
  local SCRIPT="$2"
  local LABEL="$3"

  if [[ ! -f "$FILE" ]]; then
    error "Fichier introuvable sur le VPS : $FILE"
  fi

  local BASENAME
  BASENAME=$(basename "$FILE")
  local CONTAINER_PATH="/tmp/${BASENAME}"

  log "Copie de ${BASENAME} dans le conteneur..."
  docker cp "$FILE" "${CONTAINER_ID}:${CONTAINER_PATH}"

  log "Lancement de l'import ${LABEL}..."
  docker compose exec -T backend python3 "$SCRIPT" "$CONTAINER_PATH"

  log "Nettoyage du fichier temporaire..."
  docker compose exec -T backend rm -f "$CONTAINER_PATH"

  success "Import ${LABEL} terminé !"
}

if [[ -n "$EXCEL_FILE" ]]; then
  header "Étape 3 — Import des élèves (import_eleves)"
  log "Vérification des dépendances Python requises..."
  docker compose exec -T backend pip install --quiet pandas openpyxl psycopg2-binary python-dotenv
  run_import "$EXCEL_FILE" "scripts/import_eleves.py" "élèves"
fi

if [[ -n "$REMPLACEMENT_FILE" ]]; then
  header "Étape 3 — Import Remplacement (écoles + superviseurs + classes + élèves)"
  run_import "$REMPLACEMENT_FILE" "scripts/import_remplacement.py" "remplacement"
fi

if [[ -z "$EXCEL_FILE" && -z "$REMPLACEMENT_FILE" ]]; then
  echo ""
  success "Déploiement terminé — aucun import demandé."
  echo -e "\n💡 ${YELLOW}Options d'import disponibles :${NC}"
  echo -e "   ${CYAN}./redeploy_and_import.sh --import <fichier.xlsx>${NC}                  → import_eleves.py"
  echo -e "   ${CYAN}./redeploy_and_import.sh --import-remplacement <fichier.xlsx>${NC}     → import_remplacement.py"
  echo ""
  exit 0
fi

header "Déploiement & Import Terminés !"
success "Le backend a été redéployé et les données importées avec succès !"
echo ""
