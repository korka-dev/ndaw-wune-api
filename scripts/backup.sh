#!/usr/bin/env bash
# ==============================================================================
#  NDAW WUNE — Sauvegarde base PostgreSQL + médias (uploads)  [rapport V2, APP-48]
#
#  Usage (dans le dossier de l'API, sur le serveur) :
#    ./scripts/backup.sh                       # sauvegarde dans ./backups
#    BACKUP_DIR=/srv/nwn-backups RETENTION_DAYS=30 ./scripts/backup.sh
#
#  Produit un dossier horodaté contenant :
#    db.dump        pg_dump au format custom (restauration : scripts/restore.sh)
#    uploads.tar.gz contenu du volume des médias (photos, audios, ressources)
#    SHA256SUMS     empreintes pour vérifier l'intégrité de la copie externe
#    MANIFEST.txt   date, commit de l'API, nombre de fichiers médias
#
#  Ce script ne remplace PAS : la planification (cron/systemd), la copie hors
#  du serveur et un essai de restauration régulier — voir docs/SECURITE_EXPLOITATION.md.
#  Aucun secret n'est affiché ; le contenu est nominatif : stockage protégé obligatoire.
# ==============================================================================
set -euo pipefail

cd "$(dirname "$0")/.."
[ -f .env ] || { echo "❌ .env introuvable" >&2; exit 1; }
set -a; . ./.env; set +a

BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="$BACKUP_DIR/$STAMP"
umask 077
mkdir -p "$DEST"

echo "▶ Sauvegarde de la base ($POSTGRES_DB)…"
docker compose exec -T db pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc --no-owner > "$DEST/db.dump"

echo "▶ Sauvegarde des médias…"
docker compose exec -T backend tar -C /app/uploads -czf - . > "$DEST/uploads.tar.gz"
NB_MEDIAS="$(tar -tzf "$DEST/uploads.tar.gz" | grep -vc '/$' || true)"

( cd "$DEST" && sha256sum db.dump uploads.tar.gz > SHA256SUMS )
{
  echo "date_utc=$STAMP"
  echo "api_commit=$(git rev-parse --short HEAD 2>/dev/null || echo inconnu)"
  echo "fichiers_medias=$NB_MEDIAS"
  echo "taille_db=$(du -h "$DEST/db.dump" | cut -f1)"
} > "$DEST/MANIFEST.txt"

# Rotation locale : la copie externe garde sa propre rétention.
find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -mtime "+$RETENTION_DAYS" -exec rm -rf {} +

echo "✅ Sauvegarde terminée : $DEST"
