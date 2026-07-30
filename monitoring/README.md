# Supervision — Prometheus + Grafana

## Ce qui est supervisé

- **API FastAPI** (`backend:8000/metrics`, via `prometheus-fastapi-instrumentator`) : requêtes/s, latence (p50/p95/p99), taux d'erreur par endpoint.
- **Serveur** (`node-exporter`) : CPU, RAM, disque, réseau.
- **PostgreSQL** (`postgres-exporter`) : connexions actives, taille de la base, requêtes lentes, réplication (si applicable).
- **Redis** (`redis-exporter`) : mémoire utilisée, hit rate du cache, connexions.

9 alertes préconfigurées (voir `alert.rules.yml`) : backend/Postgres/Redis injoignables, taux d'erreur 5xx élevé, latence API dégradée, connexions Postgres proches du plafond, CPU/RAM/disque serveur critiques. Visibles dans Prometheus (`/alerts`) — aucune notification (email/Slack) n'est configurée pour l'instant, voir "Aller plus loin" en bas.

## Installation sur le VPS

Prérequis : la stack principale (`backend/docker-compose.yml`) doit déjà tourner — elle crée le réseau Docker `ndawwune-net` que cette stack rejoint pour scraper `db`, `redis` et `backend` par leur nom de service.

```bash
cd ndaw-wune/backend/monitoring
cp .env.example .env
nano .env   # définir GRAFANA_ADMIN_PASSWORD, recopier POSTGRES_*/REDIS_PASSWORD depuis ../.env
./setup_monitoring.sh
```

Puis configurer Caddy pour exposer Grafana publiquement (Prometheus, lui, reste interne — jamais exposé) :

```bash
# Créer le DNS A/AAAA pour monitoring.ndawwune.cloud avant de recharger Caddy
sudo find / -name "Caddyfile*" 2>/dev/null   # localiser le Caddyfile principal
# Importer ou fusionner Caddyfile.monitoring dedans, puis :
sudo caddy validate --config /chemin/vers/Caddyfile
sudo caddy reload   --config /chemin/vers/Caddyfile
```

Accès : `https://monitoring.ndawwune.cloud`, identifiants définis dans `.env` (`GRAFANA_ADMIN_USER`/`GRAFANA_ADMIN_PASSWORD`).

## Ajouter les dashboards

Le datasource Prometheus est déjà configuré automatiquement dans Grafana. Pour les dashboards, le plus rapide est d'importer des dashboards communautaires déjà éprouvés (Dashboards → New → Import, puis coller l'ID) :

| Dashboard | ID Grafana.com |
|---|---|
| Node Exporter Full (serveur) | `1860` |
| PostgreSQL (postgres_exporter) | `9628` |
| Redis | `763` |

Pour l'API FastAPI (pas de dashboard générique adapté), quelques requêtes PromQL utiles à mettre dans un dashboard "NDAW WUNE API" :
- Requêtes/s : `sum(rate(http_requests_total{job="backend"}[5m])) by (handler)`
- Latence p95 : `histogram_quantile(0.95, sum(rate(http_request_duration_highr_seconds_bucket{job="backend"}[5m])) by (le))`
- Taux d'erreur : `sum(rate(http_requests_total{job="backend", status="5xx"}[5m])) / sum(rate(http_requests_total{job="backend"}[5m]))`

Tout dashboard JSON exporté (Grafana → Share → Export) peut être déposé dans `grafana/provisioning/dashboards/json/` — il sera chargé automatiquement au prochain démarrage (pas besoin de le refaire manuellement après un redéploiement).

## Mettre à jour

```bash
cd ndaw-wune/backend/monitoring
git pull
docker compose pull
docker compose up -d
```

## Aller plus loin (non fait ici)

- **Notifications d'alerte** (Alertmanager + email/Slack/Telegram) — actuellement les alertes ne font qu'apparaître dans l'UI Prometheus, personne n'est prévenu activement.
- **Utilisateur PostgreSQL en lecture seule dédié** pour `postgres-exporter`, plutôt que de réutiliser le compte applicatif complet (`POSTGRES_USER`) :
  ```sql
  CREATE USER monitoring WITH PASSWORD '...' IN ROLE pg_monitor;
  ```
  puis mettre à jour `DATA_SOURCE_NAME` dans `docker-compose.yml`.
- **Rétention Prometheus** : actuellement 30 jours (`--storage.tsdb.retention.time=30d`), ajustable selon l'espace disque disponible sur le VPS.
