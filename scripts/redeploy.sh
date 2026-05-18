#!/usr/bin/env bash
set -euo pipefail

GREEN='\033[0;32m'; BLUE='\033[0;36m'; RED='\033[0;31m'; NC='\033[0m'
cd "$(dirname "${BASH_SOURCE[0]}")/.."   # → ~/ndaw-wune

echo -e "${BLUE}▶ [1/3] Build de l'image backend...${NC}"
docker compose build backend

echo -e "${BLUE}▶ [2/3] Redémarrage du backend...${NC}"
docker compose up -d --no-deps backend

echo -e "${BLUE}▶ [3/3] Healthcheck (max 2 min)...${NC}"
for i in $(seq 1 20); do
  STATUS=$(docker inspect --format='{{.State.Health.Status}}' $(docker compose ps -q backend) 2>/dev/null || echo "unknown")
  [ "$STATUS" = "healthy" ] && break
  [ "$STATUS" = "unhealthy" ] && docker compose logs --tail=30 backend && exit 1
  echo "   Essai $i/20 — $STATUS..."; sleep 6
done

[ "$STATUS" = "healthy" ] || { docker compose logs --tail=30 backend; exit 1; }
echo -e "${GREEN}✅ Déploiement réussi !${NC}"
docker compose ps
