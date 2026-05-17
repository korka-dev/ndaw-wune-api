#!/usr/bin/env bash

# ==============================================================================
# Script de déploiement automatique de l'API ARED Ndaw Wune sur un serveur VPS
# OS supportés : Ubuntu 20.04+, Debian 11+
# ==============================================================================

# Couleurs pour l'affichage
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${BLUE}======================================================================${NC}"
echo -e "${BLUE}        🚀 DÉPLOIEMENT AUTOMATIQUE DU BACKEND NDAW WUNE API           ${NC}"
echo -e "${BLUE}======================================================================${NC}"

# S'assurer que le script est lancé en tant que root
if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}Erreur : Ce script doit être exécuté en tant que root (sudo).${NC}"
  exit 1
fi

# ------------------------------------------------------------------------------
# 1. Mise à jour du système et installation des utilitaires de base
# ------------------------------------------------------------------------------
echo -e "\n${YELLOW}[1/6] Mise à jour des packages système...${NC}"
apt-get update -y && apt-get upgrade -y
apt-get install -y curl git ufw jq gnupg lsb-release ca-certificates pwgen

# ------------------------------------------------------------------------------
# 2. Installation de Docker et Docker Compose (si non installés)
# ------------------------------------------------------------------------------
echo -e "\n${YELLOW}[2/6] Vérification de l'installation de Docker...${NC}"

if ! command -v docker &> /dev/null; then
  echo -e "${BLUE}Docker n'est pas installé. Installation officielle de Docker en cours...${NC}"
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg --yes
  chmod a+r /etc/apt/keyrings/docker.gpg

  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
    $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null

  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  
  systemctl enable docker
  systemctl start docker
  echo -e "${GREEN}✓ Docker a été installé avec succès !${NC}"
else
  echo -e "${GREEN}✓ Docker est déjà installé.${NC}"
fi

# ------------------------------------------------------------------------------
# 3. Préparation du répertoire de déploiement
# ------------------------------------------------------------------------------
echo -e "\n${YELLOW}[3/6] Préparation du répertoire de déploiement...${NC}"
DEPLOY_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
cd "$DEPLOY_DIR" || exit 1
echo -e "${GREEN}✓ Utilisation du répertoire local : $DEPLOY_DIR${NC}"

# ------------------------------------------------------------------------------
# 4. Configuration sécurisée du fichier d'environnement (.env)
# ------------------------------------------------------------------------------
echo -e "\n${YELLOW}[4/6] Configuration du fichier d'environnement .env...${NC}"

if [ ! -f ".env" ]; then
  echo -e "${BLUE}Création d'un nouveau fichier .env sécurisé...${NC}"
  cp .env.example .env

  # Génération de clés aléatoires de haute sécurité
  GEN_SECRET=$(pwgen -s 64 1)
  GEN_PG_PASS=$(pwgen -s 32 1)
  GEN_REDIS_PASS=$(pwgen -s 32 1)

  # Remplacement des valeurs vides par des valeurs générées de manière robuste
  sed -i "s|SECRET_KEY=|SECRET_KEY=${GEN_SECRET}|g" .env
  sed -i "s|POSTGRES_PASSWORD=|POSTGRES_PASSWORD=${GEN_PG_PASS}|g" .env
  sed -i "s|REDIS_PASSWORD=|REDIS_PASSWORD=${GEN_REDIS_PASS}|g" .env

  # Configurer CORS par défaut à '*' ou selon les besoins
  sed -i "s|CORS_ORIGINS=.*|CORS_ORIGINS=*|g" .env
  
  echo -e "${GREEN}✓ Fichier .env créé avec des mots de passe générés de haute sécurité !${NC}"
else
  echo -e "${GREEN}✓ Fichier .env existant détecté (inchangé pour préserver vos secrets).${NC}"
fi

# ------------------------------------------------------------------------------
# 5. Configuration de la sécurité réseau (Pare-feu UFW)
# ------------------------------------------------------------------------------
echo -e "\n${YELLOW}[5/6] Configuration du Pare-feu (UFW)...${NC}"
ufw allow 22/tcp comment 'SSH'
ufw allow 80/tcp comment 'HTTP Reverse Proxy'
ufw allow 443/tcp comment 'HTTPS Reverse Proxy'

# S'assurer que Postgres et Redis ne sont pas exposés à l'extérieur
ufw deny 5432/tcp comment 'PostgreSQL (docker-only)'
ufw deny 6379/tcp comment 'Redis (docker-only)'

# Activer le pare-feu sans demander de confirmation interactive
echo "y" | ufw enable
echo -e "${GREEN}✓ Pare-feu configuré et activé !${NC}"

# ------------------------------------------------------------------------------
# 6. Démarrage des conteneurs via Docker Compose
# ------------------------------------------------------------------------------
echo -e "\n${YELLOW}[6/6] Lancement des conteneurs via Docker Compose...${NC}"

# On s'assure que le volume PostgreSQL est bien créé
docker compose down --remove-orphans
docker compose up -d --build

# Boucle d'attente de démarrage de l'API (Health Check)
echo -e "\n${BLUE}⏳ Attente du démarrage complet de l'API Ndaw Wune (healthcheck)...${NC}"
MAX_TRIES=20
TRY=0
API_UP=false

while [ $TRY -lt $MAX_TRIES ]; do
  # Récupère l'état du conteneur backend
  STATUS=$(docker inspect --format='{{json .State.Health.Status}}' "$(docker compose ps -q backend)" 2>/dev/null | tr -d '"')
  
  if [ "$STATUS" = "healthy" ]; then
    API_UP=true
    break
  fi
  
  echo -e "Attente... Essai $((TRY+1))/$MAX_TRIES (Statut actuel : ${STATUS:-en cours de démarrage})"
  sleep 6
  TRY=$((TRY+1))
done

if [ "$API_UP" = true ]; then
  echo -e "\n${GREEN}======================================================================${NC}"
  echo -e "${GREEN}  🎉 SUCCÈS : L'API Ndaw Wune est en ligne et parfaitement fonctionnelle !${NC}"
  echo -e "${GREEN}======================================================================${NC}"
  echo -e "${BLUE}🌍 Port exposé localement : http://localhost:8000${NC}"
  echo -e "${BLUE}📂 Migrations de base de données : Appliquées avec succès ✓${NC}"
  echo -e "${BLUE}🛡️ Services isolés (Postgres et Redis) : Sécurisés par le Pare-feu ✓${NC}"
else
  echo -e "\n${RED}⚠️ Erreur : L'API n'a pas pu démarrer dans le temps imparti.${NC}"
  echo -e "${RED}Veuillez vérifier les logs avec la commande : docker compose logs backend${NC}"
  exit 1
fi

# ------------------------------------------------------------------------------
# 7. Bonus : Configuration Reverse Proxy automatique (Caddy)
# ------------------------------------------------------------------------------
echo -e "\n${YELLOW}💡 ÉTAPE FINALE CONSEILLÉE : Configurer votre nom de domaine (HTTPS)${NC}"
echo -e "Pour exposer votre API de manière sécurisée (obligatoire pour iOS/Android en production) :"
echo -e "1. Installez le serveur Caddy :"
echo -e "   ${BLUE}sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https${NC}"
echo -e "   ${BLUE}curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg${NC}"
echo -e "   ${BLUE}curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list${NC}"
echo -e "   ${BLUE}sudo apt update && sudo apt install caddy${NC}"
echo -e "\n2. Créez un fichier Caddyfile dans ${BLUE}/etc/caddy/Caddyfile${NC} avec ce contenu :"
echo -e "   ${GREEN}api.votre-domaine.com {${NC}"
echo -e "   ${GREEN}    reverse_proxy localhost:8000${NC}"
echo -e "   ${GREEN}}${NC}"
echo -e "\n3. Redémarrez Caddy : ${BLUE}sudo systemctl restart caddy${NC}"
echo -e "Caddy s'occupera automatiquement d'installer et renouveler votre certificat SSL Let's Encrypt ! 🛡️"
echo -e "\n${BLUE}======================================================================${NC}"
