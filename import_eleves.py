#!/usr/bin/env python3
"""
import_eleves.py
---------------
Importe les élèves depuis le fichier Excel LISTES_ELEVES vers la base
PostgreSQL du projet ARED NdawWune.

Comportement :
  - Lit DATABASE_URL depuis /backend/.env (ou les variables POSTGRES_* séparées)
  - Récupère automatiquement la session de programme active
  - Résout chaque école par son nom (SCHOOL) — la crée si elle n'existe pas
  - Découpe le nom complet : dernier mot = nom, le reste = prénom
  - Insère les élèves en ignorant les doublons (contrainte uq_eleve_school_classe_nom)
  - Affiche un résumé : insérés / ignorés / erreurs

Usage :
  python3 import_eleves.py --file LISTES_ELEVES_NWAS_AKD_modifié.xlsx

Options :
  --file     Chemin vers le fichier Excel  (requis)
  --dry-run  Simule l'import sans écrire en base
  --env      Chemin vers le .env du backend  (défaut : ./backend/.env)
  --docker   Mode Docker : se connecte via le hostname 'db' (réseau interne Docker)
             À utiliser quand le script tourne DANS le container backend.
"""

import argparse
import os
import sys
import uuid
from pathlib import Path

# ──────────────────────────────────────────────
# Dépendances  (pip install pandas openpyxl psycopg2-binary python-dotenv)
# ──────────────────────────────────────────────
try:
    import pandas as pd
except ImportError:
    sys.exit("❌  pandas manquant — pip install pandas openpyxl")

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    sys.exit("❌  psycopg2 manquant — pip install psycopg2-binary")

try:
    from dotenv import dotenv_values
except ImportError:
    sys.exit("❌  python-dotenv manquant — pip install python-dotenv")


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def load_env(env_path: str) -> dict:
    """Charge les variables depuis le fichier .env."""
    p = Path(env_path)
    if not p.exists():
        sys.exit(f"❌  Fichier .env introuvable : {p.resolve()}")
    return dotenv_values(p)


def build_dsn(env: dict) -> str:
    """Construit la chaîne de connexion PostgreSQL depuis les variables .env."""
    # Priorité : DATABASE_URL explicite
    if "DATABASE_URL" in env:
        return env["DATABASE_URL"]

    host     = env.get("POSTGRES_HOST", "localhost")
    port     = env.get("POSTGRES_PORT", "5432")
    db       = env.get("POSTGRES_DB", "")
    user     = env.get("POSTGRES_USER", "")
    password = env.get("POSTGRES_PASSWORD", "")

    if not db or not user:
        sys.exit("❌  Variables POSTGRES_DB / POSTGRES_USER manquantes dans .env")

    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


def split_name(full_name: str) -> tuple[str, str]:
    """
    Découpe un nom complet en (prénom, nom).
    Règle : dernier mot = nom, tout le reste = prénom.
    Ex : 'NDEYE MAI DIENG' → prénom='NDEYE MAI', nom='DIENG'
    """
    parts = str(full_name).strip().split()
    if len(parts) == 1:
        return ("", parts[0])
    return (" ".join(parts[:-1]), parts[-1])


def normalize_genre(sexe: str) -> str:
    """Normalise le genre vers 'F' ou 'M'."""
    s = str(sexe).strip().lower()
    if s in ("fille", "f"):
        return "F"
    if s in ("garcon", "garçon", "g", "m"):
        return "M"
    return sexe  # valeur brute si inconnue


# ──────────────────────────────────────────────
# Import principal
# ──────────────────────────────────────────────

def run_import(excel_path: str, env_path: str, dry_run: bool, docker_mode: bool = False):
    print(f"\n{'[DRY-RUN] ' if dry_run else ''}Import élèves — {excel_path}")
    print("=" * 60)

    # 1. Lecture du fichier Excel
    print("📂  Lecture du fichier Excel…")
    df = pd.read_excel(excel_path)
    required_cols = {"SCHOOL", "IEF", "COMMUNE", "Classe", "name", "Sexe"}
    missing = required_cols - set(df.columns)
    if missing:
        sys.exit(f"❌  Colonnes manquantes dans le fichier : {missing}")

    df = df.dropna(subset=["name", "SCHOOL", "Classe"])
    print(f"   {len(df)} lignes valides à traiter")

    # 2. Connexion PostgreSQL
    env = load_env(env_path)
    dsn = build_dsn(env)

    if docker_mode:
        # Dans le container Docker, le service DB est accessible via 'db' (réseau Docker)
        # On s'assure d'utiliser le driver psycopg2 (pas asyncpg)
        final_dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
    else:
        # Hors Docker (VPS host), le port 5432 est exposé sur localhost
        final_dsn = dsn.replace("postgresql+asyncpg://", "postgresql://").replace("@db:", "@localhost:")

    print(f"🔌  Connexion à PostgreSQL…")
    try:
        conn = psycopg2.connect(final_dsn)
        conn.autocommit = False
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        print("   Connexion établie ✓")
    except psycopg2.OperationalError as e:
        hint = ("réseau Docker interne" if docker_mode else "localhost (port exposé)")
        sys.exit(f"❌  Impossible de se connecter : {e}\n"
                 f"    DSN tenté : {final_dsn}\n"
                 f"    Mode : {hint}\n"
                 f"    💡 Vérifiez que PostgreSQL est accessible (Docker up ?)")

    # 3. Session active
    print("🔎  Recherche de la session de programme active…")
    cur.execute(
        "SELECT id, name FROM program_sessions WHERE is_active = TRUE ORDER BY created_at DESC LIMIT 1"
    )
    session_row = cur.fetchone()
    if not session_row:
        conn.close()
        sys.exit("❌  Aucune session active trouvée dans program_sessions.\n"
                 "    Créez ou activez une session depuis l'interface admin.")
    session_id   = session_row["id"]
    session_name = session_row["name"]
    print(f"   Session : « {session_name} » (id={session_id})")

    # 4. Cache des écoles existantes  {nom_upper → uuid}
    print("🏫  Chargement des écoles existantes…")
    cur.execute("SELECT id, name FROM schools")
    school_cache: dict[str, uuid.UUID] = {
        row["name"].upper(): row["id"] for row in cur.fetchall()
    }
    print(f"   {len(school_cache)} écoles en base")

    # 5. Boucle d'import
    inserted = 0
    skipped  = 0
    created_schools = 0
    errors   = []

    print("\n⏳  Traitement des élèves…")

    for idx, row in df.iterrows():
        school_name = str(row["SCHOOL"]).strip()
        school_key  = school_name.upper()
        region      = str(row.get("IEF", "")).strip()
        commune     = str(row.get("COMMUNE", "")).strip()
        classe      = str(row["Classe"]).strip()
        full_name   = str(row["name"]).strip()
        sexe        = str(row.get("Sexe", "")).strip()

        prenom, nom = split_name(full_name)
        genre       = normalize_genre(sexe)

        # --- Résolution de l'école ---
        if school_key not in school_cache:
            if not dry_run:
                new_id = uuid.uuid4()
                cur.execute(
                    """
                    INSERT INTO schools (id, name, region, city, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, NOW(), NOW())
                    ON CONFLICT DO NOTHING
                    RETURNING id
                    """,
                    (str(new_id), school_name, region or None, commune or None),
                )
                row_s = cur.fetchone()
                if row_s:
                    school_cache[school_key] = row_s["id"]
                    created_schools += 1
                else:
                    # Doublon créé entre-temps — on relit
                    cur.execute("SELECT id FROM schools WHERE UPPER(name) = %s", (school_key,))
                    school_cache[school_key] = cur.fetchone()["id"]
            else:
                # dry-run : on génère un UUID fictif pour la simulation
                school_cache[school_key] = uuid.uuid4()
                created_schools += 1

        school_id = school_cache[school_key]

        # --- Insertion de l'élève ---
        if not dry_run:
            try:
                cur.execute(
                    """
                    INSERT INTO eleves
                        (id, nom, prenom, classe, genre, statut,
                         school_id, session_id, created_at, updated_at)
                    VALUES
                        (%s, %s, %s, %s, %s, 'actif',
                         %s, %s, NOW(), NOW())
                    ON CONFLICT ON CONSTRAINT uq_eleve_school_classe_nom DO NOTHING
                    """,
                    (
                        str(uuid.uuid4()),
                        nom,
                        prenom or None,
                        classe,
                        genre or None,
                        str(school_id),
                        str(session_id),
                    ),
                )
                if cur.rowcount == 1:
                    inserted += 1
                else:
                    skipped += 1
            except Exception as e:
                errors.append(f"Ligne {idx + 2} ({full_name}) : {e}")
                conn.rollback()  # On annule uniquement cette transaction partielle
        else:
            inserted += 1  # dry-run : on compte tout comme succès

    # 6. Commit ou rollback
    if not dry_run:
        if errors:
            print(f"\n⚠️   {len(errors)} erreur(s) détectée(s) — rollback complet")
            conn.rollback()
        else:
            conn.commit()
            print("   Commit effectué ✓")
    else:
        print("   [DRY-RUN] Aucune écriture effectuée")

    conn.close()

    # 7. Résumé
    print("\n" + "=" * 60)
    print("📊  RÉSUMÉ DE L'IMPORT")
    print("=" * 60)
    print(f"  Lignes traitées      : {len(df)}")
    print(f"  Élèves insérés       : {inserted}")
    print(f"  Doublons ignorés     : {skipped}")
    print(f"  Écoles créées        : {created_schools}")
    print(f"  Erreurs              : {len(errors)}")
    if errors:
        print("\nDétail des erreurs :")
        for e in errors[:20]:
            print(f"   • {e}")
        if len(errors) > 20:
            print(f"   … et {len(errors) - 20} autres")
    print("=" * 60)

    if errors:
        sys.exit(1)


# ──────────────────────────────────────────────
# Entrée principale
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Import élèves XLSX → PostgreSQL NdawWune")
    parser.add_argument(
        "--file", required=True,
        help="Chemin vers le fichier Excel (.xlsx)"
    )
    parser.add_argument(
        "--env", default="./backend/.env",
        help="Chemin vers le .env du backend (défaut : ./backend/.env)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Simule l'import sans écrire en base"
    )
    parser.add_argument(
        "--docker", action="store_true",
        help="Mode Docker : se connecte via hostname 'db' (réseau interne Docker)"
    )
    args = parser.parse_args()

    run_import(
        excel_path=args.file,
        env_path=args.env,
        dry_run=args.dry_run,
        docker_mode=args.docker,
    )


if __name__ == "__main__":
    main()
