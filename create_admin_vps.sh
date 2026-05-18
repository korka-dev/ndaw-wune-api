#!/usr/bin/env bash

# ==============================================================================
# Script de création automatique du compte administrateur adiallo@gmail.com
# A exécuter directement sur le serveur VPS.
# Ce script est 100% autonome et intègre le code Python directement.
# ==============================================================================

# Couleurs pour l'affichage
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${BLUE}======================================================================${NC}"
echo -e "${BLUE}      👤 CRÉATION COMPTE ADMINISTRATEUR SUR LE SERVEUR VPS            ${NC}"
echo -e "${BLUE}======================================================================${NC}"

# Récupérer le dossier du script
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

# Détecter et changer vers le dossier contenant le docker-compose
if [ -f "backend/docker-compose.yml" ]; then
    COMPOSE_DIR="$(pwd)/backend"
elif [ -f "docker-compose.yml" ]; then
    COMPOSE_DIR="$(pwd)"
elif [ -f "$SCRIPT_DIR/backend/docker-compose.yml" ]; then
    COMPOSE_DIR="$SCRIPT_DIR/backend"
elif [ -f "$SCRIPT_DIR/docker-compose.yml" ]; then
    COMPOSE_DIR="$SCRIPT_DIR"
else
    echo -e "${RED}Erreur : Impossible de trouver le dossier backend ou docker-compose.yml.${NC}"
    exit 1
fi

cd "$COMPOSE_DIR" || exit 1
echo -e "${GREEN}✓ Dossier de travail détecté : $COMPOSE_DIR${NC}"

# 1. Vérifier si Docker est installé
if ! command -v docker &> /dev/null; then
    echo -e "${RED}Erreur : Docker n'est pas installé sur ce serveur VPS.${NC}"
    exit 1
fi

# 2. Vérifier si le conteneur backend est en cours d'exécution
CONTAINER_NAME=$(docker compose ps -q backend 2>/dev/null)

if [ -z "$CONTAINER_NAME" ]; then
    echo -e "${YELLOW}[!] Le conteneur backend ne semble pas être démarré.${NC}"
    echo -e "${YELLOW}[*] Tentative de démarrage des conteneurs via Docker Compose...${NC}"
    docker compose up -d
    
    echo -e "⏳ Attente du démarrage de l'API (healthcheck)..."
    sleep 5
fi

# 3. Synchroniser les fichiers de migration Alembic du host vers le conteneur
if [ -d "alembic/versions" ]; then
    echo -e "${YELLOW}[*] Synchronisation du dossier alembic/versions dans le conteneur...${NC}"
    docker cp alembic/versions "$(docker compose ps -q backend)":/tmp/versions
    docker compose exec -T -u root backend mkdir -p /app/alembic/versions
    docker compose exec -T -u root backend cp -a /tmp/versions/. /app/alembic/versions/
    
    if [ $? -ne 0 ]; then
        echo -e "${RED}Erreur : Impossible de synchroniser le dossier alembic dans le conteneur.${NC}"
        exit 1
    fi
else
    echo -e "${RED}Erreur : Dossier alembic/versions introuvable sur le host dans $(pwd).${NC}"
    exit 1
fi

# 4. Appliquer les migrations de base de données (Alembic)
echo -e "\n${YELLOW}[*] Application des migrations de base de données (Alembic)...${NC}"
docker compose exec -T -u root backend alembic upgrade head

# (Étape de vérification supprimée : Alembic gère la consistance correctement maintenant)

# 6. Créer le script python directement à l'intérieur du conteneur
echo -e "\n${YELLOW}[*] Injection du script Python dans le conteneur backend (en mode root)...${NC}"

# S'assurer que le dossier /app/scripts existe
docker compose exec -T -u root backend mkdir -p /app/scripts

# Écrire le script Python via stdin dans le conteneur
docker compose exec -T -u root backend tee /app/scripts/create_admin.py > /dev/null << 'EOF'
#!/usr/bin/env python3
import asyncio
import sys
import uuid
from pathlib import Path

# Rendre "app" importable depuis la racine du projet backend
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal as async_session_factory
from app.core.security import hash_password
from app.models.user import User, UserRole, UserStatus


async def create_admin() -> None:
    email = "adiallo@gmail.com"
    password = "P@sser123"
    name = "Admin Diallo"

    print(f"[*] Connexion à la base de données...")
    async with async_session_factory() as session:
        # Vérifier si l'utilisateur existe déjà
        existing = await session.scalar(
            select(User).where(User.email == email)
        )
        
        if existing:
            print(f"[!] L'utilisateur avec l'e-mail '{email}' existe déjà.")
            print(f"[*] Mise à jour du mot de passe et rôle admin pour '{email}'...")
            existing.role = UserRole.admin
            existing.status = UserStatus.actif
            existing.password_hash = hash_password(password)
            existing.must_change_password = False
            await session.commit()
            print(f"[+] ✅ Compte admin '{email}' mis à jour avec le mot de passe : {password}")
            return

        # Création du compte administrateur
        new_admin = User(
            id=uuid.uuid4(),
            name=name,
            email=email,
            password_hash=hash_password(password),
            role=UserRole.admin,
            status=UserStatus.actif,
            must_change_password=False,
        )
        
        session.add(new_admin)
        await session.commit()
        print(f"[+] ✅ Compte admin '{email}' créé avec succès !")
        print(f"    - E-mail/Identifiant : {email}")
        print(f"    - Mot de passe : {password}")
        print(f"    - Rôle : {UserRole.admin.value}")


def main() -> None:
    try:
        asyncio.run(create_admin())
    except Exception as e:
        print(f"[❌] Une erreur est survenue lors de la création du compte : {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
EOF

if [ $? -ne 0 ]; then
    echo -e "${RED}Erreur : Impossible d'injecter le script Python dans le conteneur.${NC}"
    exit 1
fi

# 7. Exécuter le script Python de création d'admin à l'intérieur du conteneur
echo -e "${YELLOW}[*] Exécution du script de création d'administrateur dans le conteneur (en mode root)...${NC}"

docker compose exec -T -u root backend python scripts/create_admin.py

if [ $? -eq 0 ]; then
    echo -e "\n${GREEN}======================================================================${NC}"
    echo -e "${GREEN}🎉 Opération terminée avec succès sur votre VPS !${NC}"
    echo -e "${GREEN}Vous pouvez désormais vous connecter avec :${NC}"
    echo -e "   - E-mail : ${BLUE}adiallo@gmail.com${NC}"
    echo -e "   - Mot de passe : ${BLUE}P@sser123${NC}"
    echo -e "${GREEN}======================================================================${NC}"
else
    echo -e "\n${RED}⚠️ Erreur : L'exécution du script de création d'admin a échoué.${NC}"
    echo -e "Veuillez vérifier les logs avec la commande : docker compose logs backend"
    exit 1
fi
