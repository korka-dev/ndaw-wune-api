#!/usr/bin/env bash
# ==============================================================================
#  NDAW WUNE — Sauvegarde base PostgreSQL + médias (uploads)  [rapport V2, APP-48]
#
#  Usage (dans le dossier de l'API, sur le serveur) :
#    ./scripts/backup.sh                       # sauvegarde dans ./backups
#    BACKUP_DIR=/srv/nwn-backups RETENTION_DAYS=30 ./scripts/backup.sh
#    BACKUP_REMOTE=sauvegarde@stockage:/nwn ./scripts/backup.sh   # + copie externe
#
#  Produit un dossier horodaté contenant :
#    db.dump        pg_dump au format custom (restauration : scripts/restore.sh)
#    uploads.tar.gz contenu du volume des médias (photos, audios, ressources)
#    SHA256SUMS     empreintes pour vérifier l'intégrité de la copie externe
#    MANIFEST.txt   date, commit de l'API, nombre de fichiers médias
#    EFFECTIFS.txt  nombre de lignes des tables principales (preuve de restauration)
#
#  Planification : deploy/systemd/ndawwune-backup.timer. Essai de restauration
#  isolé : scripts/test_restauration.sh. Voir docs/DEPLOIEMENT_ARED.md.
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

# Effectifs de référence : comparés automatiquement par scripts/test_restauration.sh.
docker compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -F= -c "$(cat scripts/effectifs.sql)" > "$DEST/EFFECTIFS.txt"

( cd "$DEST" && sha256sum db.dump uploads.tar.gz EFFECTIFS.txt > SHA256SUMS )
{
  echo "date_utc=$STAMP"
  echo "api_commit=$(git rev-parse --short HEAD 2>/dev/null || echo inconnu)"
  echo "fichiers_medias=$NB_MEDIAS"
  echo "taille_db=$(du -h "$DEST/db.dump" | cut -f1)"
} > "$DEST/MANIFEST.txt"

# Rotation locale : la copie externe garde sa propre rétention.
find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -mtime "+$RETENTION_DAYS" -exec rm -rf {} +

# Copie hors du serveur (APP-48) : BACKUP_REMOTE=utilisateur@hote:/chemin (rsync/ssh)
# ou rclone:distant:chemin. Échec de copie = code de sortie non nul (alerte cron).
if [ -n "${BACKUP_REMOTE:-}" ]; then
  echo "▶ Copie externe vers ${BACKUP_REMOTE%%:*}:…"
  case "$BACKUP_REMOTE" in
    rclone:*) rclone copy "$DEST" "${BACKUP_REMOTE#rclone:}/$STAMP" ;;
    *)        rsync -a --chmod=D700,F600 "$DEST" "$BACKUP_REMOTE/" ;;
  esac
  echo "copie_externe=ok" >> "$DEST/MANIFEST.txt"
else
  echo "⚠️  BACKUP_REMOTE non défini : la sauvegarde reste sur ce serveur (insuffisant, APP-48)." >&2
fi

echo "✅ Sauvegarde terminée : $DEST"
