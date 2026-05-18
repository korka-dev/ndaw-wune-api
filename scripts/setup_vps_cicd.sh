#!/usr/bin/env bash
# ==============================================================================
#  NDAW WUNE — Initialisation CI/CD sur le VPS (à lancer UNE SEULE FOIS)
#
#  Ce script :
#    1. Clone le repo GitHub sur le VPS
#    2. Crée le fichier .env depuis .env.example
#    3. Lance le premier déploiement
#
#  Usage :
#    ssh user@votre-vps "bash -s" < backend/scripts/setup_vps_cicd.sh
#  ou depuis le VPS directement :
#    bash setup_vps_cicd.sh
# ==============================================================================

set -euo pipefail

# ════════════════════════════════════════════════════════════════
#  ⚙️  CONFIGURATION — Modifiez ces valeurs avant d'exécuter
# ════════════════════════════════════════════════════════════════

REPO_URL="https://github.com/VOTRE_USER/VOTRE_REPO.git"   # ← Remplacez
APP_DIR="/opt/ndaw-wune"                                    # ← Dossier sur le VPS
BRANCH="main"                                               # ← Branche à déployer

# ════════════════════════════════════════════════════════════════

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;36m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

log()  { echo -e "${BLUE}▶ $*${NC}"; }
ok()   { echo -e "${GREEN}✅ $*${NC}"; }
warn() { echo -e "${YELLOW}⚠  $*${NC}"; }
err()  { echo -e "${RED}❌ $*${NC}"; exit 1; }

echo ""
echo -e "${BOLD}══════════════════════════════════════════════════════${NC}"
echo -e "${BOLD}   NDAW WUNE — Initialisation CI/CD sur le VPS        ${NC}"
echo -e "${BOLD}══════════════════════════════════════════════════════${NC}"
echo ""

# ── Vérification Docker ───────────────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
  warn "Docker non trouvé. Lancement de deploy_vps.sh pour l'installation..."
  # Télécharger et lancer le script d'installation si besoin
  err "Installez Docker d'abord (sudo bash backend/deploy_vps.sh) puis relancez ce script."
fi
ok "Docker disponible."

# ── 1. Cloner le repo ─────────────────────────────────────────────────────────
log "[1/4] Clonage du dépôt dans $APP_DIR..."

if [ -d "$APP_DIR/.git" ]; then
  warn "Le dépôt existe déjà dans $APP_DIR. Mise à jour..."
  cd "$APP_DIR"
  git fetch --all --prune
  git checkout "$BRANCH"
  git reset --hard "origin/$BRANCH"
else
  sudo mkdir -p "$APP_DIR"
  sudo chown "$(whoami)":"$(whoami)" "$APP_DIR"
  git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
  cd "$APP_DIR"
fi

ok "Code cloné → $APP_DIR"

# ── 2. Créer le fichier .env ──────────────────────────────────────────────────
log "[2/4] Configuration du fichier .env..."

ENV_FILE="$APP_DIR/backend/.env"
ENV_EXAMPLE="$APP_DIR/backend/.env.example"

if [ -f "$ENV_FILE" ]; then
  warn ".env existant détecté → conservé sans modification."
else
  if [ ! -f "$ENV_EXAMPLE" ]; then
    err "backend/.env.example introuvable dans le repo !"
  fi

  cp "$ENV_EXAMPLE" "$ENV_FILE"

  # Générer les clés secrètes automatiquement si pwgen est disponible
  if command -v pwgen &>/dev/null; then
    SECRET_KEY=$(pwgen -s 64 1)
    PG_PASS=$(pwgen -s 32 1)
    REDIS_PASS=$(pwgen -s 32 1)
    sed -i "s|^SECRET_KEY=.*|SECRET_KEY=$SECRET_KEY|" "$ENV_FILE"
    sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$PG_PASS|" "$ENV_FILE"
    sed -i "s|^REDIS_PASSWORD=.*|REDIS_PASSWORD=$REDIS_PASS|" "$ENV_FILE"
    ok "Secrets générés automatiquement."
  else
    warn "pwgen non disponible. Éditez manuellement $ENV_FILE avant de continuer."
  fi

  echo ""
  echo -e "${YELLOW}📝 IMPORTANT : Éditez le fichier .env et vérifiez :"
  echo -e "   - SECRET_KEY (min 64 caractères aléatoires)"
  echo -e "   - POSTGRES_PASSWORD"
  echo -e "   - CORS_ORIGINS (ex: https://votre-domaine.com)"
  echo -e "   Fichier : $ENV_FILE${NC}"
  echo ""
  read -r -p "   Appuyez sur [Entrée] une fois le fichier .env configuré..."
fi

ok ".env prêt."

# ── 3. Premier déploiement ────────────────────────────────────────────────────
log "[3/4] Premier déploiement..."

chmod +x "$APP_DIR/backend/scripts/redeploy.sh"
bash "$APP_DIR/backend/scripts/redeploy.sh"

# ── 4. Résumé final ───────────────────────────────────────────────────────────
PUBLIC_IP=$(curl -sf https://api.ipify.org 2>/dev/null || echo "IP_INCONNUE")

echo ""
echo -e "${BOLD}${GREEN}══════════════════════════════════════════════════════════════${NC}"
echo -e "${BOLD}${GREEN}   🎉 VPS prêt pour le CI/CD automatique !                    ${NC}"
echo -e "${BOLD}${GREEN}══════════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  📍 Dossier app     : ${BLUE}$APP_DIR${NC}"
echo -e "  🌍 IP publique     : ${BLUE}$PUBLIC_IP${NC}"
echo -e "  🔗 API locale      : ${BLUE}http://localhost:8000${NC}"
echo ""
echo -e "${YELLOW}  ═══ Configurez maintenant les Secrets GitHub ══════════════${NC}"
echo -e "  Allez dans : GitHub → votre repo → Settings → Secrets → Actions"
echo ""
echo -e "  Secret            Valeur"
echo -e "  ─────────────     ──────────────────────────────────────"
echo -e "  VPS_HOST          ${BLUE}$PUBLIC_IP${NC}     (ou votre domaine)"
echo -e "  VPS_USER          ${BLUE}$(whoami)${NC}"
echo -e "  VPS_PASSWORD      ${BLUE}[votre mot de passe SSH]${NC}"
echo -e "  VPS_PORT          ${BLUE}22${NC}             (ou votre port SSH)"
echo -e "  VPS_APP_DIR       ${BLUE}$APP_DIR${NC}"
echo ""
echo -e "${GREEN}  Une fois les secrets ajoutés : poussez un commit sur main${NC}"
echo -e "${GREEN}  et le déploiement se lancera automatiquement. ✅${NC}"
echo ""
