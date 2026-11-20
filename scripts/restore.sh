#!/usr/bin/env bash
# ==============================================================================
#  NDAW WUNE — Restauration d'une sauvegarde produite par scripts/backup.sh  [APP-48]
#
#  À exécuter d'abord sur un environnement de RECETTE isolé, jamais directement
#  sur la production sans décision de l'exploitant.
#
#  Usage :  CONFIRM=oui ./scripts/restore.sh backups/20261120T020000Z
#
#  1. vérifie les empreintes SHA256 ;
#  2. remplace la base (pg_restore --clean) ;
#  3. remplace le contenu du volume des médias ;
#  4. affiche les effectifs principaux pour comparaison avec la source.
# ==============================================================================
set -euo pipefail

SRC="${1:?Indiquez le dossier de sauvegarde à restaurer}"
[ "${CONFIRM:-}" = "oui" ] || { echo "❌ Restauration destructive : relancez avec CONFIRM=oui" >&2; exit 1; }

cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
SRC="$(cd "$SRC" && pwd)"

echo "▶ Vérification des empreintes…"
( cd "$SRC" && sha256sum -c SHA256SUMS )

START=$(date +%s)
echo "▶ Restauration de la base…"
docker compose exec -T db pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner < "$SRC/db.dump"

echo "▶ Restauration des médias…"
docker compose exec -T -u root backend sh -c 'find /app/uploads -mindepth 1 -delete'
docker compose exec -T -u root backend tar -C /app/uploads -xzf - < "$SRC/uploads.tar.gz"
docker compose exec -T -u root backend chown -R appuser:appgroup /app/uploads

echo "▶ Effectifs après restauration :"
docker compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -c "
  SELECT 'users', count(*) FROM users UNION ALL
  SELECT 'eleves', count(*) FROM eleves UNION ALL
  SELECT 'rapports_journalier', count(*) FROM rapports_journalier UNION ALL
  SELECT 'seances', count(*) FROM seances;"
echo "fichiers_medias=$(docker compose exec -T backend sh -c 'find /app/uploads -type f | wc -l')"
grep fichiers_medias "$SRC/MANIFEST.txt" | sed 's/^/source : /'

echo "✅ Restauration terminée en $(( $(date +%s) - START )) s — consigner la date et la durée (preuve APP-48)."
