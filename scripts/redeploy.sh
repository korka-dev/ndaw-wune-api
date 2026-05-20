#!/usr/bin/env bash
# ==============================================================================
#  NDAW WUNE — Script de redéploiement Backend
#
#  Usage : cd ndaw-wune && bash ndaw-wune/scripts/redeploy.sh
# ==============================================================================

set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;36m'; RED='\033[0;31m'; BOLD='\033[1m'; NC='\033[0m'
log()  { echo -e "${BLUE}▶ $*${NC}"; }
ok()   { echo -e "${GREEN}✅ $*${NC}"; }
warn() { echo -e "${YELLOW}⚠  $*${NC}"; }
err()  { echo -e "${RED}❌ $*${NC}"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$BACKEND_DIR"

echo ""
echo -e "${BOLD}══════════════════════════════════════════════${NC}"
echo -e "${BOLD}   NDAW WUNE — Redéploiement Backend          ${NC}"
echo -e "${BOLD}══════════════════════════════════════════════${NC}"
echo ""

# ── Pré-requis ────────────────────────────────────────────────────────────────
if ! command -v docker &>/dev/null; then err "Docker non installé."; exit 1; fi
if [ ! -f ".env" ]; then err "ndaw-wune/.env manquant !"; exit 1; fi

# ── 1. Build ──────────────────────────────────────────────────────────────────
log "[1/4] Build de l'image Docker..."
docker compose build backend
ok "Image buildée."
echo ""

# ── 2. db + redis ─────────────────────────────────────────────────────────────
log "[2/4] Démarrage de db et redis..."
docker compose up -d db redis

for i in $(seq 1 15); do
  docker compose exec -T db pg_isready &>/dev/null && break
  echo "   PostgreSQL pas encore prêt ($i/15)..."; sleep 3
done
ok "db et redis actifs."
echo ""

# ── 3. Migrations ─────────────────────────────────────────────────────────────
log "[3/4] Migrations Alembic..."
docker compose run --rm --no-deps backend alembic upgrade head
ok "Migrations appliquées."
echo ""

# ── 4. Redémarrage backend ────────────────────────────────────────────────────
log "[4/4] Redémarrage du backend..."
docker compose up -d --no-deps backend

CONTAINER=$(docker compose ps -q backend 2>/dev/null)
for i in $(seq 1 20); do
  STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null || echo "unknown")
  case "$STATUS" in
    healthy)   break ;;
    unhealthy) err "Backend unhealthy. Logs :"; docker compose logs --tail=50 backend; exit 1 ;;
    *)         echo "   Attente... ($i/20)"; sleep 6 ;;
  esac
done

[ "$STATUS" = "healthy" ] || { err "Timeout healthcheck."; docker compose logs --tail=50 backend; exit 1; }
ok "Backend healthy !"
echo ""

docker image prune -f 2>/dev/null || true

echo -e "${BOLD}${GREEN}══════════════════════════════════════════════${NC}"
echo -e "${BOLD}${GREEN}   ✅ Redéploiement terminé !                  ${NC}"
echo -e "${BOLD}${GREEN}══════════════════════════════════════════════${NC}"
echo ""
docker compose ps
echo ""
curl -sf https://api.ndawwune.cloud/health && echo "" || warn "Health check HTTPS non accessible"
echo ""
