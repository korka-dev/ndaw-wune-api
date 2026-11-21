#!/usr/bin/env bash
# ==============================================================================
#  NDAW WUNE — Essai de restauration ISOLÉ (rapport V2, APP-48)
#
#  Restaure une sauvegarde de scripts/backup.sh dans un PostgreSQL jetable
#  (conteneur temporaire, aucun port publié) et un dossier temporaire, puis
#  produit une preuve datée et chronométrée :
#    - empreintes SHA256 vérifiées ;
#    - effectifs restaurés = effectifs enregistrés à la sauvegarde ;
#    - fichiers médias restaurés = nombre annoncé ;
#    - chaque fichier référencé en base (audios, ressources) est présent.
#  La production n'est jamais touchée. Sans arguments : dernière sauvegarde.
#
#  Usage :  ./scripts/test_restauration.sh [backups/20261120T020000Z]
#  Sortie :  <sauvegarde>/RESTAURATION_<date>.txt  + code de sortie non nul si écart.
# ==============================================================================
set -euo pipefail

cd "$(dirname "$0")/.."
BACKUP_DIR="${BACKUP_DIR:-./backups}"
SRC="${1:-$( (ls -1d "$BACKUP_DIR"/*/ 2>/dev/null || true) | sort | tail -n1)}"
[ -n "$SRC" ] && [ -f "$SRC/db.dump" ] || { echo "❌ Aucune sauvegarde trouvée (${SRC:-$BACKUP_DIR})" >&2; exit 1; }
SRC="$(cd "$SRC" && pwd)"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RAPPORT="$SRC/RESTAURATION_$STAMP.txt"
NOM="nwn-restauration-$STAMP"
TMP="$(mktemp -d)"
ECARTS=0
trap 'docker rm -f "$NOM" >/dev/null 2>&1 || true; rm -rf "$TMP"' EXIT

log() { echo "$*" | tee -a "$RAPPORT"; }
umask 077
: > "$RAPPORT"
log "Essai de restauration isolé — $STAMP (UTC)"
log "Sauvegarde : $(basename "$SRC")"
log "$(grep -E '^(date_utc|api_commit|fichiers_medias)=' "$SRC/MANIFEST.txt" | tr '\n' ' ')"

DEBUT=$(date +%s)
log "1. Empreintes"
( cd "$SRC" && sha256sum -c SHA256SUMS ) >> "$RAPPORT" 2>&1 || { log "❌ empreintes invalides"; exit 1; }

log "2. Base (PostgreSQL jetable)"
docker run -d --name "$NOM" -e POSTGRES_PASSWORD=restauration -e POSTGRES_DB=restauration postgres:16-alpine >/dev/null
for _ in $(seq 1 60); do docker exec "$NOM" pg_isready -U postgres -d restauration >/dev/null 2>&1 && break; sleep 1; done
docker exec -i "$NOM" pg_restore -U postgres -d restauration --no-owner --exit-on-error < "$SRC/db.dump"
docker exec -i "$NOM" psql -U postgres -d restauration -At -F= < scripts/effectifs.sql > "$TMP/effectifs_restaures.txt"
if [ -f "$SRC/EFFECTIFS.txt" ]; then
  if diff -u "$SRC/EFFECTIFS.txt" "$TMP/effectifs_restaures.txt" >> "$RAPPORT"; then
    log "   ✅ effectifs identiques : $(tr '\n' ' ' < "$TMP/effectifs_restaures.txt")"
  else
    log "   ❌ effectifs différents (voir diff ci-dessus)"; ECARTS=$((ECARTS + 1))
  fi
else
  log "   ⚠️  sauvegarde antérieure à EFFECTIFS.txt : effectifs restaurés seulement affichés"
  log "   $(tr '\n' ' ' < "$TMP/effectifs_restaures.txt")"
fi

log "3. Médias"
mkdir -p "$TMP/uploads"
tar -C "$TMP/uploads" -xzf "$SRC/uploads.tar.gz"
NB="$(find "$TMP/uploads" -type f | wc -l | tr -d ' ')"
ATTENDU="$(grep '^fichiers_medias=' "$SRC/MANIFEST.txt" | cut -d= -f2)"
if [ "$NB" = "$ATTENDU" ]; then log "   ✅ $NB fichiers restaurés"; else log "   ❌ $NB fichiers restaurés, $ATTENDU annoncés"; ECARTS=$((ECARTS + 1)); fi

docker exec -i "$NOM" psql -U postgres -d restauration -At > "$TMP/references.txt" <<'SQL'
SELECT audio_filename FROM evaluation_tirages WHERE audio_filename IS NOT NULL
UNION SELECT audio_filename FROM evaluation_resultats WHERE audio_filename IS NOT NULL
UNION SELECT audio_filename FROM evaluation_resultat_historique WHERE audio_filename IS NOT NULL
UNION SELECT stored_filename FROM documents;
SQL
MANQUANTS=0
while IFS= read -r f; do
  [ -z "$f" ] && continue
  [ -f "$TMP/uploads/$f" ] || [ -n "$(find "$TMP/uploads" -type f -name "$(basename "$f")" -print -quit)" ] || { MANQUANTS=$((MANQUANTS + 1)); echo "   manquant : $f" >> "$RAPPORT"; }
done < "$TMP/references.txt"
REFS="$(grep -c . "$TMP/references.txt" || true)"
if [ "$MANQUANTS" -eq 0 ]; then log "   ✅ $REFS fichiers référencés en base, tous présents"; else log "   ❌ $MANQUANTS/$REFS fichiers référencés absents"; ECARTS=$((ECARTS + 1)); fi

DUREE=$(( $(date +%s) - DEBUT ))
log "Durée totale : ${DUREE} s"
if [ "$ECARTS" -eq 0 ]; then log "RÉSULTAT : ✅ restauration conforme"; else log "RÉSULTAT : ❌ $ECARTS écart(s)"; fi
echo "Preuve : $RAPPORT"
exit "$ECARTS"
