# Test de charge — NDAW WUNE Backend

## Pourquoi

Répondre à « le système tient-il face à X connexions simultanées ? » avec des
chiffres mesurés plutôt qu'une estimation. Deux volets :

1. **Capacité configurée** (déjà en place, voir plus bas) — workers Gunicorn,
   pool de connexions DB, `max_connections` Postgres.
2. **Vérification empirique** — ce script k6 (`k6-script.js`).

## Calibrage actuel (VPS 4 vCPU / 8 Go RAM)

| Paramètre | Valeur | Où |
|---|---|---|
| `GUNICORN_WORKERS` | 9 (règle 2×vCPU+1) | `Dockerfile`, `.env` |
| `MAX_CONNECTIONS_POOL` (par worker) | 15 | `.env` |
| `MAX_OVERFLOW` (par worker) | 5 | `.env` |
| Connexions DB max théoriques | (15+5) × 9 = **180** | calculé |
| Postgres `max_connections` | 220 (180 + marge admin/migrations) | `docker-compose.yml` |
| Postgres `shared_buffers` | 512 MB | `docker-compose.yml` |

**Si le VPS change de taille**, recalculer :
- `GUNICORN_WORKERS` = 2 × vCPU + 1
- Garder `(MAX_CONNECTIONS_POOL + MAX_OVERFLOW) × GUNICORN_WORKERS` **inférieur** à
  `max_connections` Postgres, avec au moins ~30-40 de marge.
- `shared_buffers` ≈ 25% de la RAM totale, en laissant de la place pour Redis
  (256 MB max déjà configuré), les processus Gunicorn eux-mêmes, et l'OS.

Ces changements de `.env`/`docker-compose.yml` ne s'appliquent qu'après un
`docker compose up -d --build` (ou `./redeploy_and_import.sh`) — pensez à
répercuter les nouvelles valeurs dans le `.env` du VPS, `.env.example` n'étant
qu'un modèle versionné.

## Lancer le test

**Jamais contre la production** (`api.ndawwune.cloud`) sans fenêtre de
maintenance — un test à pleine charge peut saturer le pool et impacter de
vrais utilisateurs. Cible un environnement local ou une copie de staging.

```bash
# 1. Backend + DB + Redis doivent tourner (local : ./start.sh, ou docker compose up -d)
# 2. Lancer le test (ajuster IDENTIFIER/PASSWORD selon l'environnement ciblé)
k6 run \
  -e BASE_URL=http://localhost:8000 \
  -e IDENTIFIER=adiallo@gmail.com \
  -e PASSWORD='P@sser123' \
  -e TARGET_VUS=200 \
  backend/loadtest/k6-script.js
```

Monter progressivement `TARGET_VUS` (200 → 500 → 1000), en surveillant entre
chaque palier :
```bash
docker stats                                   # CPU/RAM des conteneurs
docker compose logs --tail=50 db               # erreurs Postgres (connexions refusées, etc.)
docker compose exec db psql -U ared_user -d ared_ndawune -c \
  "SELECT count(*) FROM pg_stat_activity;"      # connexions DB actives en direct
```

## Lire les résultats

k6 affiche en fin de run :
- `http_req_failed` : taux d'erreurs — doit rester **< 1%**. Une remontée
  brutale à un palier donné = c'est le plafond réel de connexions simultanées.
- `http_req_duration{endpoint:dashboard}` p(95) : latence de l'endpoint
  DB-lourd — une explosion de latence signale la saturation du pool avant
  même les erreurs franches.
- `http_req_duration{endpoint:health}` p(95) : latence brute HTTP (sans DB) —
  sert de référence ; si elle aussi se dégrade, le goulot n'est plus la DB
  mais le CPU/la bande passante du serveur lui-même.

Un test local (un seul process backend en dev, pas les 9 workers Gunicorn de
prod) donnera un plafond plus bas que la vraie capacité VPS — normal, ce n'est
qu'un test de fumée pour valider le script. Pour un chiffre représentatif de
la production, lancer le même test contre une copie de staging tournant avec
la config Docker/Gunicorn réelle (`docker compose up -d --build backend`).
