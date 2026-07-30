#!/usr/bin/env bash
# ==============================================================================
#  NDAW WUNE — Démarrage/mise à jour de la stack de supervision
#
#  Usage (depuis backend/monitoring/, après un git pull) :
#    ./setup_monitoring.sh
#
#  Prérequis :
#    - La stack principale (backend/docker-compose.yml) doit déjà tourner —
#      elle crée le réseau "ndawwune-net" que cette stack rejoint.
#    - backend/monitoring/.env doit exister (cp .env.example .env, puis éditer).
# ==============================================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

log()     { echo -e "${CYAN}▶  $*${NC}"; }
success() { echo -e "${GREEN}✅  $*${NC}"; }
err()     { echo -e "${RED}❌  $*${NC}"; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

[ -f ".env" ] || err ".env introuvable. Lancez : cp .env.example .env && nano .env"

if ! docker network inspect ndawwune-net >/dev/null 2>&1; then
  err "Réseau 'ndawwune-net' introuvable — démarrez d'abord la stack principale (cd .. && docker compose up -d)."
fi

log "Démarrage de la stack de supervision (Prometheus, Grafana, exporters)..."
docker compose pull
docker compose up -d

log "Attente de Prometheus..."
for i in $(seq 1 15); do
  curl -sf http://127.0.0.1:9090/-/ready >/dev/null 2>&1 && break
  echo "  ($i/15)..."; sleep 2
done

log "Attente de Grafana..."
for i in $(seq 1 15); do
  curl -sf http://127.0.0.1:3001/api/health >/dev/null 2>&1 && break
  echo "  ($i/15)..."; sleep 2
done

echo ""
docker compose ps
echo ""
success "Supervision en ligne."
echo "  Grafana    : http://127.0.0.1:3001 (ou https://monitoring.ndawwune.cloud une fois Caddy configuré)"
echo "  Prometheus : http://127.0.0.1:9090 (interne uniquement — tunnel SSH pour y accéder)"
echo "  Logs       : docker compose logs -f"
