#!/usr/bin/env bash
# ==============================================================================
#  NDAW WUNE — Contrôle d'un déploiement (rapport V2, APP-42 / APP-45)
#
#  Usage (depuis n'importe quel poste) :
#    API_URL=https://api.exemple.org ADMIN_ORIGIN=https://admin.exemple.org \
#      ./scripts/verifier_deploiement.sh
#
#  Vérifie, sans identifiants ni données personnelles :
#    1. DNS et certificat HTTPS de l'API ;
#    2. /health (disponibilité) et /health/ready (base, migrations, Redis, médias) ;
#    3. CORS : l'origine du dashboard est autorisée, une origine étrangère refusée ;
#    4. politique de version mobile (/api/v1/app-version) ;
#    5. /metrics n'est pas public.
#  Code de sortie non nul au premier contrôle bloquant : utilisable en recette
#  et après chaque bascule ou retour arrière.
# ==============================================================================
set -uo pipefail

API_URL="${API_URL:?Indiquez API_URL (ex. https://api.exemple.org)}"
API_URL="${API_URL%/}"
API_URL="${API_URL%/api/v1}"
ADMIN_ORIGIN="${ADMIN_ORIGIN:-}"
ECHECS=0

ok()   { echo "  ✅ $*"; }
ko()   { echo "  ❌ $*"; ECHECS=$((ECHECS + 1)); }
info() { echo "  ℹ️  $*"; }

HOTE="$(echo "$API_URL" | sed -E 's#^https?://([^/:]+).*#\1#')"
echo "▶ API : $API_URL"

echo "1. DNS et HTTPS"
if command -v dig >/dev/null && [ -n "$(dig +short "$HOTE")" ]; then
  ok "DNS $HOTE → $(dig +short "$HOTE" | tr '\n' ' ')"
elif getent hosts "$HOTE" >/dev/null 2>&1; then
  ok "DNS $HOTE résolu"
else
  info "résolution DNS non vérifiable localement (dig/getent absents) — le test HTTPS suit"
fi
case "$API_URL" in
  https://*) curl -sS -o /dev/null --max-time 10 "$API_URL/health" && ok "certificat HTTPS valide" || ko "HTTPS en échec (certificat, DNS ou Caddy)";;
  *) ko "API_URL n'est pas en HTTPS";;
esac

echo "2. Santé"
CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$API_URL/health")"
[ "$CODE" = "200" ] && ok "/health 200" || ko "/health → $CODE"
CORPS="$(curl -s --max-time 15 -w '\n%{http_code}' "$API_URL/health/ready")"
CODE="$(echo "$CORPS" | tail -n1)"
if [ "$CODE" = "200" ]; then
  ok "/health/ready 200 — $(echo "$CORPS" | head -n1 | python3 -c 'import json,sys; d=json.load(sys.stdin); print("version", d.get("version"), "·", ", ".join("%s=%s" % (k, v.get("etat")) for k, v in d["controles"].items()))' 2>/dev/null)"
else
  ko "/health/ready → $CODE : $(echo "$CORPS" | head -n1)"
fi

echo "3. CORS"
preflight() {
  curl -s -o /dev/null -D - --max-time 10 -X OPTIONS "$API_URL/api/v1/auth/login" \
    -H "Origin: $1" -H "Access-Control-Request-Method: POST" \
    -H "Access-Control-Request-Headers: content-type" | tr -d '\r' | grep -i '^access-control-allow-origin:' | cut -d' ' -f2
}
if [ -n "$ADMIN_ORIGIN" ]; then
  [ "$(preflight "$ADMIN_ORIGIN")" = "$ADMIN_ORIGIN" ] && ok "origine du dashboard autorisée ($ADMIN_ORIGIN)" \
    || ko "origine du dashboard REFUSÉE ($ADMIN_ORIGIN) : vérifier CORS_ORIGINS"
else
  info "ADMIN_ORIGIN non fourni : autorisation du dashboard non testée"
fi
ETRANGERE="https://origine-non-autorisee.invalid"
[ -z "$(preflight "$ETRANGERE")" ] && ok "origine étrangère refusée" || ko "origine étrangère AUTORISÉE : CORS trop large"

echo "4. Version mobile"
V="$(curl -s --max-time 10 "$API_URL/api/v1/app-version")"
[ -n "$V" ] && ok "/api/v1/app-version : $V" || ko "/api/v1/app-version injoignable"

echo "5. Métriques"
CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$API_URL/metrics")"
[ "$CODE" = "404" ] && ok "/metrics non public" || ko "/metrics → $CODE (doit rester interne)"

echo
if [ "$ECHECS" -eq 0 ]; then echo "✅ Déploiement conforme ($API_URL)"; else echo "❌ $ECHECS contrôle(s) en échec"; fi
exit "$ECHECS"
