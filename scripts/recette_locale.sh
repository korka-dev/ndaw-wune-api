#!/usr/bin/env bash
# ==============================================================================
#  NDAW WUNE — Recette locale du lot L5 sur une copie de la production
#
#    ./scripts/recette_locale.sh up <sauvegarde.dump>   # restaure, contrôle, migre, démarre
#    ./scripts/recette_locale.sh status                 # santé et versions
#    ./scripts/recette_locale.sh down                   # arrête et SUPPRIME la copie
#
#  Étapes de `up` (rien n'est envoyé hors du poste) :
#    1. PostgreSQL + Redis isolés, restauration du pg_dump (-Fc) de production ;
#    2. collisions d'e-mails (APP-35) AVANT migration : arrêt si collision ;
#    3. alembic upgrade head chronométré (version de départ affichée) ;
#    4. doublons d'écoles (rapport, rien n'est modifié) ;
#    5. API sur http://localhost:8100, contrôle /health/ready ;
#    6. compte admin de recette (mot de passe temporaire affiché une fois).
#  Dashboard : voir la fin de la sortie (npm run dev sur le port 3100).
# ==============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."
DC=(docker compose -f deploy/recette/docker-compose.yml)
ETAT=".recette.env"   # secret de session de la recette (ignoré par git)

case "${1:-}" in
  up)
    DUMP="${2:?Indiquez la sauvegarde de production (fichier pg_dump -Fc)}"
    [ -f "$DUMP" ] || { echo "❌ $DUMP introuvable" >&2; exit 1; }
    if [ ! -f "$ETAT" ]; then umask 077; echo "RECETTE_SECRET_KEY=$(openssl rand -hex 64)" > "$ETAT"; fi
    set -a; . "./$ETAT"; set +a
    export APP_COMMIT="$(git rev-parse --short HEAD)"

    echo "▶ 1/6 Base et Redis isolés"
    "${DC[@]}" up -d --wait db redis
    echo "▶ Restauration de $(basename "$DUMP") ($(du -h "$DUMP" | cut -f1))"
    "${DC[@]}" exec -T db pg_restore -U recette -d recette --no-owner --no-acl --clean --if-exists < "$DUMP" \
      || echo "⚠️  pg_restore a signalé des avertissements (souvent des rôles absents : sans effet)"
    "${DC[@]}" exec -T db psql -U recette -d recette -At -F= < scripts/effectifs.sql 2>/dev/null | sed 's/^/   /' || true
    DEPART="$("${DC[@]}" exec -T db psql -U recette -d recette -At -c 'SELECT version_num FROM alembic_version' 2>/dev/null || echo aucune)"
    echo "   révision Alembic de la production : $DEPART"

    echo "▶ 2/6 Image de l'API (code $APP_COMMIT)"
    "${DC[@]}" build -q backend

    echo "▶ 3/6 Collisions d'e-mails (APP-35) — avant migration"
    SORTIE="$("${DC[@]}" run --rm --no-deps -T backend python -m scripts.collisions_identifiants 2>&1)"
    echo "$SORTIE" | sed 's/^/   /'
    if ! echo "$SORTIE" | grep -q "Aucune collision"; then
      echo "❌ Collisions à arbitrer avant la migration L3 (voir docs/MIGRATION_L3_INTEGRITE.md)." >&2
      exit 2
    fi

    echo "▶ 4/6 Migrations ($DEPART → head)"
    DEBUT=$(date +%s)
    "${DC[@]}" run --rm --no-deps -T backend alembic upgrade head
    echo "   migrations appliquées en $(( $(date +%s) - DEBUT )) s"
    "${DC[@]}" run --rm --no-deps -T backend alembic check

    echo "▶ 5/6 Doublons d'écoles (rapport, rien n'est modifié)"
    "${DC[@]}" run --rm --no-deps -T backend python -m scripts.doublons_ecoles 2>&1 | sed 's/^/   /' | head -60

    echo "▶ 6/6 API"
    "${DC[@]}" up -d backend
    for _ in $(seq 1 40); do curl -fsS http://localhost:8100/health/ready >/dev/null 2>&1 && break; sleep 2; done
    curl -s http://localhost:8100/health/ready; echo
    echo "▶ Compte admin de recette"
    MDP="Recette-$(openssl rand -hex 6)"
    "${DC[@]}" exec -T -e ADMIN_EMAIL=recette.admin@recette.invalid -e ADMIN_NAME="Admin recette" \
      -e ADMIN_PASSWORD="$MDP" backend python scripts/create_admin.py 2>&1 | sed 's/^/   /' || true
    echo "   identifiant : recette.admin@recette.invalid — mot de passe temporaire : $MDP"
    echo "   (changement demandé à la première connexion ; s'il existait déjà, il est inchangé)"
    cat <<TXT

✅ Recette prête — API : http://localhost:8100  (docs : http://localhost:8100/docs)
   Dashboard (dans ndaw-wune-admin) :
     NEXT_PUBLIC_API_URL=http://localhost:8100/api/v1 API_URL=http://localhost:8100/api/v1 npx next dev -p 3100
   Fin de recette (supprime la copie des données) : ./scripts/recette_locale.sh down
TXT
    ;;
  status)
    "${DC[@]}" ps
    curl -s http://localhost:8100/health/ready; echo
    ;;
  down)
    "${DC[@]}" down -v --remove-orphans
    rm -f "$ETAT"
    echo "✅ Recette arrêtée, copie des données supprimée."
    ;;
  *) sed -n '2,20p' "$0"; exit 1 ;;
esac
