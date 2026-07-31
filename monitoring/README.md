# Supervision — Prometheus + Grafana

## Ce qui est supervisé

- **API FastAPI** (`backend:8000/metrics`, via `prometheus-fastapi-instrumentator`) : requêtes/s, latence (p50/p95/p99), taux d'erreur par endpoint.
- **Serveur** (`node-exporter`) : CPU, RAM, disque, réseau.
- **PostgreSQL** (`postgres-exporter`) : connexions actives, taille de la base, requêtes lentes, réplication (si applicable).
- **Redis** (`redis-exporter`) : mémoire utilisée, hit rate du cache, connexions.

9 alertes préconfigurées (voir `alert.rules.yml`) : backend/Postgres/Redis injoignables, taux d'erreur 5xx élevé, latence API dégradée, connexions Postgres proches du plafond, CPU/RAM/disque serveur critiques. Visibles dans Prometheus (`/alerts`) et envoyées par email via Alertmanager (voir ci-dessous).

## Installation sur le VPS

Prérequis : la stack principale (`backend/docker-compose.yml`) doit déjà tourner — elle crée le réseau Docker `ndawwune-net` que cette stack rejoint pour scraper `db`, `redis` et `backend` par leur nom de service.

```bash
cd ndaw-wune/backend/monitoring
cp .env.example .env
nano .env   # définir GRAFANA_ADMIN_PASSWORD, recopier POSTGRES_*/REDIS_PASSWORD depuis ../.env

cp alertmanager.yml.example alertmanager.yml
nano alertmanager.yml   # identifiants SMTP + adresse destinataire (voir "Notifications par email" ci-dessous)

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

## Notifications par email (Alertmanager)

`alertmanager.yml` (jamais commité — contient un mot de passe) définit où envoyer les alertes :

```yaml
global:
  smtp_smarthost: 'smtp.gmail.com:587'
  smtp_from: 'ndawwune-alerts@example.com'
  smtp_auth_username: 'ndawwune-alerts@example.com'
  smtp_auth_password: 'un-mot-de-passe-application'
  smtp_require_tls: true

route:
  receiver: email
  ...

receivers:
  - name: email
    email_configs:
      - to: 'votre-email@example.com'
```

**Avec Gmail** : `smtp_auth_password` doit être un "mot de passe d'application" (Compte Google → Sécurité → Validation en 2 étapes → Mots de passe des applications), pas le mot de passe du compte. Avec un autre fournisseur (Mailgun, SendGrid, OVH…), adapter `smtp_smarthost`/`smtp_auth_username`/`smtp_auth_password` selon leur documentation.

Après une modification de `alertmanager.yml`, recharger sans tout redémarrer :
```bash
docker compose restart alertmanager
```

**Tester l'envoi** sans attendre qu'une vraie alerte se déclenche :
```bash
curl -H "Content-Type: application/json" -d '[{
  "labels": {"alertname": "TestAlert", "severity": "warning"},
  "annotations": {"summary": "Test manuel — à ignorer"}
}]' http://127.0.0.1:9093/api/v2/alerts
```
Un email doit arriver en 30-60s (délai `group_wait`). Interface web Alertmanager (silences, historique) accessible via tunnel SSH : `ssh -L 9093:localhost:9093 user@vps` puis `http://localhost:9093`.

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

- **Utilisateur PostgreSQL en lecture seule dédié** pour `postgres-exporter`, plutôt que de réutiliser le compte applicatif complet (`POSTGRES_USER`) :
  ```sql
  CREATE USER monitoring WITH PASSWORD '...' IN ROLE pg_monitor;
  ```
  puis mettre à jour `DATA_SOURCE_NAME` dans `docker-compose.yml`.
- **Rétention Prometheus** : actuellement 30 jours (`--storage.tsdb.retention.time=30d`), ajustable selon l'espace disque disponible sur le VPS.
