# ─────────────────────────────────────────────────────────────────────────────
# Makefile — ARED NdawWune Backend
# Usage : make <target>
# ─────────────────────────────────────────────────────────────────────────────

.PHONY: help up down build logs shell migrate seed superuser test lint format

# Fichier .env et docker-compose
DC = docker compose -f docker-compose.yml

# ── Aide ─────────────────────────────────────────────────────────────────────
help:
	@echo ""
	@echo "  ARED NdawWune — Backend"
	@echo ""
	@echo "  make up         — Démarrer tous les services (API, DB, Redis)"
	@echo "  make down       — Arrêter et supprimer les conteneurs"
	@echo "  make build      — Reconstruire l'image Docker"
	@echo "  make logs       — Afficher les logs de l'API"
	@echo "  make shell      — Ouvrir un shell dans le conteneur API"
	@echo "  make migrate    — Appliquer les migrations Alembic"
	@echo "  make seed       — Créer le compte admin initial"
	@echo "  make create-admin — Créer le compte admin spécifique (adiallo)"
	@echo "  make test       — Lancer les tests pytest"
	@echo "  make lint       — Vérifier le code avec ruff"
	@echo "  make format     — Formater le code avec ruff"
	@echo ""

# ── Docker ────────────────────────────────────────────────────────────────────
up:
	$(DC) up -d

down:
	$(DC) down

build:
	$(DC) build --no-cache

logs:
	$(DC) logs -f backend

shell:
	$(DC) exec backend bash

# ── Base de données ───────────────────────────────────────────────────────────
migrate:
	$(DC) exec backend alembic upgrade head

# Générer une nouvelle révision (usage : make revision MSG="description")
revision:
	$(DC) exec backend alembic revision --autogenerate -m "$(MSG)"

seed:
	$(DC) exec backend python scripts/seed.py

create-admin:
	$(DC) exec backend python scripts/create_admin.py

# ── Qualité du code ───────────────────────────────────────────────────────────
test:
	$(DC) exec backend pytest -v --tb=short

lint:
	$(DC) exec backend ruff check app/

format:
	$(DC) exec backend ruff format app/

# ── Raccourcis démarrage rapide ───────────────────────────────────────────────
# Lance, migre et seed en une seule commande (première utilisation)
bootstrap: up
	@echo "Attente du démarrage de la DB…"
	@sleep 5
	$(MAKE) migrate
	$(MAKE) seed

