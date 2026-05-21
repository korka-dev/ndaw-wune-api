#!/usr/bin/env bash
# =============================================================================
# deploy_and_import.sh — Redéploie le backend NdawWune sur le VPS
#                         et exécute l'import des élèves (une seule fois)
#
# Usage (depuis /home/ubuntu/ndawwune/) :
#   ./deploy_and_import.sh                                    # redéploie seulement
#   ./deploy_and_import.sh --import ~/eleves.xlsx             # redéploie + import
#   ./deploy_and_import.sh --import ~/eleves.xlsx --dry-run   # test sans écrire
#
# Prérequis sur le VPS :
#   - Docker + Docker Compose installés
#   - Le dépôt cloné dans /home/ubuntu/ndawwune/
#   - Le fichier .env présent dans /home/ubuntu/ndawwune/
# =============================================================================

set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────────
# Tout est au même endroit : le script, docker-compose.yml, .env et le repo git
BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$BACKEND_DIR"

EXCEL_FILE=""
DRY_RUN=false

# ── Couleurs ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${CYAN}▶  $*${NC}"; }
success() { echo -e "${GREEN}✅  $*${NC}"; }
warn()    { echo -e "${YELLOW}⚠️   $*${NC}"; }
error()   { echo -e "${RED}❌  $*${NC}"; exit 1; }
header()  { echo -e "\n${BOLD}${CYAN}══════════════════════════════════════════${NC}"; echo -e "${BOLD}   $*${NC}"; echo -e "${BOLD}${CYAN}══════════════════════════════════════════${NC}"; }

# ── Parsing des arguments ─────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --import)  EXCEL_FILE="$2"; shift 2 ;;
    --dry-run) DRY_RUN=true;    shift   ;;
    *) error "Argument inconnu : $1\nUsage : $0 [--import fichier.xlsx] [--dry-run]" ;;
  esac
done

# ── Vérifications préliminaires ───────────────────────────────────────────────
header "ARED NdawWune — Déploiement VPS"
echo -e "  Dossier : ${CYAN}${BACKEND_DIR}${NC}"

[[ -f "$BACKEND_DIR/.env" ]]               || error "Fichier .env absent dans $BACKEND_DIR"
[[ -f "$BACKEND_DIR/docker-compose.yml" ]] || error "docker-compose.yml absent dans $BACKEND_DIR"
command -v docker >/dev/null || error "Docker n'est pas installé."
command -v git    >/dev/null || error "Git n'est pas installé."

# ── Étape 1 : git pull ────────────────────────────────────────────────────────
# header "Étape 1/4 — Récupération du code"
# cd "$REPO_DIR"
# info "git pull origin main…"
# git pull origin main
# success "Code à jour"

# ── Étape 2 : rebuild Docker ──────────────────────────────────────────────────
header "Étape 2/4 — Rebuild et redémarrage"
cd "$BACKEND_DIR"

info "Arrêt des containers…"
docker compose down

info "Rebuild de l'image (sans cache)…"
docker compose build --no-cache

info "Démarrage des services…"
docker compose up -d
success "Services démarrés"

# ── Étape 3 : attendre que le backend soit prêt ───────────────────────────────
header "Étape 3/4 — Attente du démarrage (migrations incluses)"
MAX_WAIT=120
WAITED=0
until docker compose exec -T backend curl -sf http://localhost:8000/health > /dev/null 2>&1; do
  if [[ $WAITED -ge $MAX_WAIT ]]; then
    warn "Timeout atteint (${MAX_WAIT}s). Logs :"
    docker compose logs --tail=50 backend
    error "Le backend n'a pas démarré à temps."
  fi
  echo -n "."
  sleep 3
  WAITED=$((WAITED + 3))
done
echo ""
success "Backend opérationnel (${WAITED}s)"

info "Migration Alembic courante :"
docker compose exec -T backend alembic current 2>/dev/null || true

# ── Étape 4 : import des élèves (optionnel) ───────────────────────────────────
if [[ -z "$EXCEL_FILE" ]]; then
  header "Déploiement terminé"
  success "Redéploiement complet. Aucun import demandé."
  echo ""
  echo -e "  Pour importer les élèves ensuite :"
  echo -e "  ${CYAN}./deploy_and_import.sh --import ~/LISTES_ELEVES.xlsx${NC}"
  exit 0
fi

header "Étape 4/4 — Import des élèves"

[[ -f "$EXCEL_FILE" ]] || error "Fichier Excel introuvable : $EXCEL_FILE"

IMPORT_SCRIPT="${BACKEND_DIR}/import_eleves.py"
[[ -f "$IMPORT_SCRIPT" ]] || error "Script import_eleves.py introuvable dans $BACKEND_DIR\n   Assure-toi d'avoir pushé les dernières modifs."

EXCEL_BASENAME=$(basename "$EXCEL_FILE")
CONTAINER_EXCEL="/tmp/${EXCEL_BASENAME}"
CONTAINER_SCRIPT="/tmp/import_eleves.py"

CONTAINER_ID=$(docker compose ps -q backend)
[[ -n "$CONTAINER_ID" ]] || error "Container backend introuvable."

info "Copie des fichiers dans le container…"
docker cp "$EXCEL_FILE"    "${CONTAINER_ID}:${CONTAINER_EXCEL}"
docker cp "$IMPORT_SCRIPT" "${CONTAINER_ID}:${CONTAINER_SCRIPT}"
success "Fichiers copiés"

info "Installation des dépendances d'import…"
docker compose exec -T backend pip install --quiet pandas openpyxl psycopg2-binary python-dotenv

DRY_FLAG=""
$DRY_RUN && DRY_FLAG="--dry-run"

info "Lancement de l'import${DRY_RUN:+ (DRY-RUN — aucune écriture)}…"
docker compose exec -T backend python3 "$CONTAINER_SCRIPT" \
  --file "$CONTAINER_EXCEL" \
  --env /app/.env \
  --docker \
  $DRY_FLAG

# Nettoyage
docker compose exec -T backend rm -f "$CONTAINER_EXCEL" "$CONTAINER_SCRIPT"

header "Terminé"
if $DRY_RUN; then
  warn "Mode dry-run — aucune donnée écrite en base."
  echo -e "  Pour importer pour de vrai :"
  echo -e "  ${CYAN}./deploy_and_import.sh --import $EXCEL_FILE${NC}"
else
  success "Déploiement + import terminés avec succès !"
fi
