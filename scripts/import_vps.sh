#!/usr/bin/env bash
# ==============================================================================
#  NDAW WUNE — Import enseignants & superviseurs sur VPS
#
#  Usage (depuis le dossier backend/ sur le VPS) :
#    chmod +x scripts/import_vps.sh
#    ./scripts/import_vps.sh /chemin/vers/fichier.xlsx
#
#  Ce script installe automatiquement les dépendances Python manquantes,
#  puis lance l'import depuis le fichier Excel fourni.
# ==============================================================================

set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; RED='\033[0;31m'; NC='\033[0m'

log()     { echo -e "${CYAN}▶  $*${NC}"; }
success() { echo -e "${GREEN}✅ $*${NC}"; }
warn()    { echo -e "${YELLOW}⚠️  $*${NC}"; }
err()     { echo -e "${RED}❌ $*${NC}"; exit 1; }

# ── Argument : chemin vers le fichier Excel ───────────────────────────────────
XLSX="${1:-}"
[ -n "$XLSX" ] || err "Usage : ./scripts/import_vps.sh /chemin/vers/fichier.xlsx"
[ -f "$XLSX"  ] || err "Fichier introuvable : $XLSX"
XLSX="$(cd "$(dirname "$XLSX")" && pwd)/$(basename "$XLSX")"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(dirname "$SCRIPT_DIR")"
cd "$BACKEND_DIR"

# ── Vérifier que docker-compose.yml est là ────────────────────────────────────
[ -f "docker-compose.yml" ] || err "docker-compose.yml introuvable. Lance ce script depuis ~/ndaw-wune/"

# ── Environnement virtuel Python ──────────────────────────────────────────────
VENV_DIR="$BACKEND_DIR/.venv-import"

if [ ! -d "$VENV_DIR" ]; then
  log "Création de l'environnement virtuel Python..."
  python3 -m venv "$VENV_DIR"
  success "Environnement virtuel créé : $VENV_DIR"
fi

PYTHON="$VENV_DIR/bin/python"
PIP="$VENV_DIR/bin/pip"

# ── Installer les dépendances dans le venv ────────────────────────────────────
log "Vérification des dépendances Python..."

MISSING=()
"$PYTHON" -c "import pandas"   2>/dev/null || MISSING+=("pandas")
"$PYTHON" -c "import bcrypt"   2>/dev/null || MISSING+=("bcrypt")
"$PYTHON" -c "import openpyxl" 2>/dev/null || MISSING+=("openpyxl")

if [ ${#MISSING[@]} -gt 0 ]; then
  warn "Modules manquants : ${MISSING[*]}"
  log "Installation dans le venv..."
  "$PIP" install --quiet "${MISSING[@]}"
  success "Dépendances installées."
else
  success "Dépendances Python OK."
fi

# ── Lancer l'import ───────────────────────────────────────────────────────────
echo ""
log "Lancement de l'import depuis : $XLSX"
echo ""
"$PYTHON" scripts/import_acteurs_nwv2026.py "$XLSX"
