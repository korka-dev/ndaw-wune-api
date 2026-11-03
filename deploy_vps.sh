#!/usr/bin/env bash
# ==============================================================================
#  NDAW WUNE — Installation complète de l'API sur un VPS neuf
#  OS supportés : Ubuntu 20.04+, Debian 11+
#
#  Sur le VPS :
#    git clone https://github.com/korka-dev/ndaw-wune-api.git /opt/ndawwune/api
#    cd /opt/ndawwune/api
#    sudo bash deploy_vps.sh
#
#  Le script est idempotent (relançable sans risque) :
#    1. installe Docker, le pare-feu et Caddy s'ils manquent ;
#    2. génère le .env (secrets aléatoires, dimensionnement selon vCPU/RAM)
#       uniquement s'il n'existe pas — un .env existant n'est jamais modifié ;
#    3. construit et démarre db + redis + backend ;
#    4. publie l'API en HTTPS via Caddy (certificat Let's Encrypt automatique).
#
#  Options (variables d'environnement) :
#    API_DOMAIN=api.exemple.com     domaine public de l'API (défaut : api.ndawwune.cloud)
#    ADMIN_DOMAIN=admin.exemple.com domaine du dashboard, autorisé en CORS (défaut : admin.ndawwune.cloud)
#    SKIP_CADDY=1                ne pas installer/configurer Caddy
#    SKIP_FIREWALL=1             ne pas toucher au pare-feu UFW
#
#  Mises à jour ensuite : ./redeploy_backend.sh (ou le CI/CD GitHub Actions).
# ==============================================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

log()     { echo -e "${CYAN}▶  $*${NC}"; }
success() { echo -e "${GREEN}✅ $*${NC}"; }
warn()    { echo -e "${YELLOW}⚠️  $*${NC}"; }
err()     { echo -e "${RED}❌ $*${NC}"; exit 1; }
header()  { echo -e "\n${BOLD}${CYAN}══════════════════════════════════════════${NC}"; echo -e "${BOLD}   $*${NC}"; echo -e "${BOLD}${CYAN}══════════════════════════════════════════${NC}"; }

API_DOMAIN="${API_DOMAIN:-api.ndawwune.cloud}"
ADMIN_DOMAIN="${ADMIN_DOMAIN:-admin.ndawwune.cloud}"

[ "$EUID" -eq 0 ] || err "Ce script doit être lancé en root : sudo bash deploy_vps.sh"

BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$BACKEND_DIR"

# Remplace (ou ajoute) KEY=VALUE dans .env
set_env() {
  if grep -q "^$1=" .env; then
    sed -i "s|^$1=.*|$1=$2|" .env
  else
    echo "$1=$2" >> .env
  fi
}

header "NDAW WUNE — Installation de l'API ($API_DOMAIN)"

# ── 1. Paquets système + Docker ───────────────────────────────────────────────
header "Étape 1/5 — Paquets système et Docker"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y curl git ufw openssl ca-certificates gnupg lsb-release

if ! command -v docker &>/dev/null; then
  log "Installation de Docker (dépôt officiel)..."
  . /etc/os-release   # ID = ubuntu | debian
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL "https://download.docker.com/linux/${ID}/gpg" | gpg --dearmor --yes -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/${ID} $(lsb_release -cs) stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
  success "Docker installé."
else
  success "Docker déjà installé ($(docker --version))."
fi
docker compose version &>/dev/null || apt-get install -y docker-compose-plugin

# ── 2. Fichier .env ───────────────────────────────────────────────────────────
header "Étape 2/5 — Configuration (.env)"
ADMIN_PASSWORD_GENERATED=""

if [ -f ".env" ]; then
  success ".env existant conservé (secrets inchangés)."
else
  cp .env.example .env

  # Dimensionnement selon le VPS — même règles que backend/loadtest/README.md :
  # workers = 2×vCPU+1, et (pool+overflow)×workers + 40 de marge = max_connections.
  CPUS=$(nproc)
  RAM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
  WORKERS=$((2 * CPUS + 1))
  if   [ "$WORKERS" -le 9 ];  then PER_WORKER=20
  elif [ "$WORKERS" -le 17 ]; then PER_WORKER=15
  else                             PER_WORKER=12
  fi
  OVERFLOW=5
  POOL=$((PER_WORKER - OVERFLOW))
  PG_MAX_CONN=$((WORKERS * PER_WORKER + 40))
  PG_SHARED_BUFFERS=$((RAM_MB / 16))
  REDIS_MAX=$((RAM_MB / 32)); [ "$REDIS_MAX" -lt 256 ] && REDIS_MAX=256

  ADMIN_PASSWORD_GENERATED="$(openssl rand -hex 8)"

  set_env APP_ENV                  production
  set_env SECRET_KEY               "$(openssl rand -hex 64)"
  set_env POSTGRES_PASSWORD        "$(openssl rand -hex 24)"
  set_env CORS_ORIGINS             "https://${ADMIN_DOMAIN}"
  set_env ADMIN_PASSWORD           "$ADMIN_PASSWORD_GENERATED"
  set_env GUNICORN_WORKERS         "$WORKERS"
  set_env MAX_CONNECTIONS_POOL     "$POOL"
  set_env MAX_OVERFLOW             "$OVERFLOW"
  set_env POSTGRES_MAX_CONNECTIONS "$PG_MAX_CONN"
  set_env POSTGRES_SHARED_BUFFERS  "${PG_SHARED_BUFFERS}MB"
  set_env REDIS_MAXMEMORY          "${REDIS_MAX}mb"
  chmod 600 .env

  success ".env généré : ${CPUS} vCPU / ${RAM_MB} Mo → ${WORKERS} workers, pool ${POOL}+${OVERFLOW}, Postgres max_connections=${PG_MAX_CONN}, shared_buffers=${PG_SHARED_BUFFERS}MB."
fi

# ── 3. Pare-feu ───────────────────────────────────────────────────────────────
header "Étape 3/5 — Pare-feu (UFW)"
if [ "${SKIP_FIREWALL:-0}" = "1" ]; then
  warn "SKIP_FIREWALL=1 — pare-feu ignoré."
else
  # Ports SSH : 22 + tout port sur lequel sshd écoute + celui de la session en
  # cours — évite de se bloquer hors du serveur si SSH n'est pas sur 22.
  # SSH_CONNECTION est absent dans certaines sessions (Cursor, console web…).
  SSH_CONN="${SSH_CONNECTION:-}"
  SSH_PORTS="22 $(ss -tlnp 2>/dev/null | awk '/sshd/ {n=split($4,a,":"); print a[n]}') ${SSH_CONN##* }"
  for port in $(echo "$SSH_PORTS" | tr ' ' '\n' | grep -E '^[0-9]+$' | sort -un); do
    ufw allow "${port}/tcp" comment 'SSH' >/dev/null
  done
  ufw allow 80/tcp  comment 'HTTP (Caddy)'  >/dev/null
  ufw allow 443/tcp comment 'HTTPS (Caddy)' >/dev/null
  ufw --force enable >/dev/null
  success "UFW actif : SSH, 80 et 443 ouverts (Postgres/Redis/API restent sur 127.0.0.1)."
fi

# ── 4. Conteneurs ─────────────────────────────────────────────────────────────
header "Étape 4/5 — Build et démarrage (db, redis, backend)"
docker compose pull db redis
docker compose up -d --build

log "Attente du healthcheck (migrations Alembic incluses)..."
STATUS="starting"
for i in $(seq 1 30); do
  STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$(docker compose ps -q backend)" 2>/dev/null || echo "starting")
  [ "$STATUS" = "healthy" ] && break
  echo "  ($i/30) statut : $STATUS"
  sleep 6
done
if [ "$STATUS" != "healthy" ]; then
  docker compose logs --tail=60 backend
  err "L'API n'est pas healthy. Voir les logs ci-dessus."
fi
success "API en ligne sur 127.0.0.1:8000."

# ── 5. Caddy (HTTPS) ──────────────────────────────────────────────────────────
header "Étape 5/5 — Reverse proxy HTTPS (Caddy)"
if [ "${SKIP_CADDY:-0}" = "1" ]; then
  warn "SKIP_CADDY=1 — Caddy ignoré."
else
  if ! command -v caddy &>/dev/null; then
    log "Installation de Caddy..."
    apt-get install -y debian-keyring debian-archive-keyring apt-transport-https
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -y
    apt-get install -y caddy
  fi

  # Caddyfile principal = simple import de /etc/caddy/sites/*.caddy, partagé
  # avec le dashboard admin. Le Caddyfile d'exemple installé par le paquet
  # (page d'accueil sur :80) est remplacé ; un Caddyfile personnalisé est conservé.
  mkdir -p /etc/caddy/sites
  if [ ! -f /etc/caddy/Caddyfile ] || grep -q "/usr/share/caddy" /etc/caddy/Caddyfile; then
    echo "import /etc/caddy/sites/*.caddy" > /etc/caddy/Caddyfile
  elif ! grep -q "import /etc/caddy/sites/\*.caddy" /etc/caddy/Caddyfile; then
    echo -e "\nimport /etc/caddy/sites/*.caddy" >> /etc/caddy/Caddyfile
  fi

  sed "s/api\.ndawwune\.cloud/${API_DOMAIN}/g" Caddyfile.api > /etc/caddy/sites/ndawwune-api.caddy

  if caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then
    systemctl enable --now caddy >/dev/null
    systemctl reload caddy
    success "Caddy configuré pour https://${API_DOMAIN}"
  else
    caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile || true
    warn "Config Caddy invalide (un domaine est peut-être déjà défini dans /etc/caddy/Caddyfile) — Caddy non rechargé."
  fi
fi

# ── Résumé ────────────────────────────────────────────────────────────────────
header "Installation terminée"
docker compose ps
echo ""
success "API : https://${API_DOMAIN}/health  (le DNS de ${API_DOMAIN} doit pointer vers ce VPS)"
if [ -n "$ADMIN_PASSWORD_GENERATED" ]; then
  echo ""
  echo -e "${BOLD}Compte administrateur créé :${NC}"
  echo -e "   E-mail       : $(grep '^ADMIN_EMAIL=' .env | cut -d= -f2-)"
  echo -e "   Mot de passe : ${BOLD}${ADMIN_PASSWORD_GENERATED}${NC}"
  echo -e "   (aussi stocké dans .env → ADMIN_PASSWORD ; changez-le après la première connexion)"
fi
echo ""
log "Mises à jour : ./redeploy_backend.sh   |   Logs : docker compose logs -f backend"
