#!/usr/bin/env bash
# ==============================================================================
#  NDAW WUNE — Surveillance de /health/ready et alertes (rapport V2, APP-45)
#
#  Lancé toutes les 2 minutes par deploy/systemd/ndawwune-sante.timer.
#  N'alerte qu'au CHANGEMENT d'état (prêt → dégradé, puis retour), sur le
#  webhook du canal convenu (ALERTE_WEBHOOK_URL). Le message contient
#  l'environnement, les contrôles en échec et leur code court : jamais de
#  secret, d'URL interne ni de donnée personnelle.
#
#    ./scripts/surveiller_sante.sh                 # contrôle + alerte si changement
#    ./scripts/surveiller_sante.sh --alerte "txt"  # alerte manuelle (sauvegarde…)
#    ./scripts/surveiller_sante.sh --test          # alerte de test (recette APP-45)
# ==============================================================================
set -uo pipefail

SANTE_URL="${SANTE_URL:-http://127.0.0.1:8000/health/ready}"
ENV_NOM="${ALERTE_ENVIRONNEMENT:-inconnu}"
ETAT_FICHIER="${ETAT_FICHIER:-/var/tmp/ndawwune-sante.etat}"

alerter() {
  local texte="[NDAW WUNE · $ENV_NOM · $(hostname -s)] $1"
  echo "$texte"
  logger -t ndawwune-sante -- "$texte" 2>/dev/null || true
  if [ -n "${ALERTE_WEBHOOK_URL:-}" ]; then
    python3 -c 'import json,sys; print(json.dumps({"text": sys.argv[1]}))' "$texte" \
      | curl -sS -m 10 -X POST -H 'Content-Type: application/json' --data-binary @- "$ALERTE_WEBHOOK_URL" >/dev/null \
      || echo "⚠️  webhook d'alerte injoignable" >&2
  else
    echo "⚠️  ALERTE_WEBHOOK_URL non défini : alerte seulement dans le journal système" >&2
  fi
}

case "${1:-}" in
  --alerte) alerter "${2:-alerte sans message}"; exit 0 ;;
  --test)   alerter "Alerte de TEST (APP-45) — aucune action requise"; exit 0 ;;
esac

REPONSE="$(curl -s -m 15 -w '\n%{http_code}' "$SANTE_URL" || true)"
CODE="$(echo "$REPONSE" | tail -n1)"
CORPS="$(echo "$REPONSE" | sed '$d')"

if [ "$CODE" = "200" ]; then
  ETAT="pret"; DETAIL=""
else
  ETAT="degrade"
  DETAIL="$(echo "$CORPS" | python3 -c 'import json,sys
try:
    d = json.load(sys.stdin)
    print(", ".join("%s=%s" % (k, v.get("code")) for k, v in d.get("controles", {}).items() if v.get("etat") != "ok"))
except Exception:
    print("API injoignable")' 2>/dev/null)"
  [ -z "$DETAIL" ] && DETAIL="API injoignable (HTTP ${CODE:-000})"
fi

PRECEDENT="$(cat "$ETAT_FICHIER" 2>/dev/null || echo inconnu)"
echo "$ETAT" > "$ETAT_FICHIER"

if [ "$ETAT" != "$PRECEDENT" ]; then
  if [ "$ETAT" = "pret" ]; then
    [ "$PRECEDENT" != "inconnu" ] && alerter "✅ Service rétabli : toutes les dépendances répondent."
  else
    alerter "❌ Service dégradé : $DETAIL. Diagnostic : docker compose ps ; docker compose logs --tail=100 backend."
  fi
fi
[ "$ETAT" = "pret" ]
