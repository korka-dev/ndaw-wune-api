#!/usr/bin/env python3
"""
Import Langues.xlsx + Assignation_Traitement_Simple_NWV2026.xlsx → Base NDAW WUNE
===================================================================================
Fichier 1 — Langues.xlsx
    Colonnes : code_ecole | Ecole | Langue
    → Renseigne School.langue (uniquement si actuellement vide, pour ne pas
      écraser une saisie manuelle admin). Ne crée aucune école.

Fichier 2 — Assignation_Traitement_Simple_NWV2026.xlsx
    Colonnes : unit | code_ecole | classe_num | ief | commune | nom_enseignant | treat
    → Pour chaque ligne :
        - Si un enseignant du même nom (normalisé) existe déjà à l'école
          (code_ecole) → met à jour son groupe_recherche si besoin.
        - Sinon → CRÉE l'enseignant (rattaché à l'école, classe déduite de
          classe_num parmi les classes existantes de l'école, mot de passe
          par défaut, changement obligatoire à la première connexion) avec
          le bon groupe_recherche.
      "treat" → "traitement" (app avec minuteur) si la valeur commence par
      "Traitement", "controle" (app sans minuteur) si elle commence par
      "Controle"/"Contrôle".

Idempotent : relancer le script ne recrée pas les enseignants déjà importés
(match par nom normalisé + école) et ne fait que corriger les champs
manquants, sans écraser une valeur déjà renseignée.

Usage (local ou production) :
    python scripts/import_langues_et_groupes_2026.py <Langues.xlsx> <Assignation.xlsx>
    (en production, s'assurer que DATABASE_URL pointe vers la bonne base)
"""

import asyncio
import os
import sys
import uuid
from pathlib import Path

import pandas as pd
from sqlalchemy import select

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

_DEFAULT_DB = "postgresql+asyncpg://ared_user:ared_secret@localhost:5432/ared_ndawune"
if not os.path.exists("/.dockerenv") and (
    "DATABASE_URL" not in os.environ
    or "://db:" in os.environ.get("DATABASE_URL", "")
):
    os.environ["DATABASE_URL"] = os.environ.get("DATABASE_URL", _DEFAULT_DB).replace(
        "@db:", "@localhost:"
    )
    if os.environ["DATABASE_URL"].startswith("postgresql+asyncpg://$"):
        os.environ["DATABASE_URL"] = _DEFAULT_DB

from app.core.database import AsyncSessionLocal
from app.core.security import hash_password
from app.models.school import School
from app.models.school_classe import SchoolClasse
from app.models.user import User, UserGroupe, UserRole, UserStatus

DEFAULT_PASSWORD = "Passer123"


def _normalize_name(name: str) -> str:
    return " ".join(str(name).strip().split()).upper()


def _map_treat(treat: str) -> UserGroupe | None:
    t = str(treat).strip().lower()
    if t.startswith("traitement"):
        return UserGroupe.traitement
    if t.startswith("controle") or t.startswith("contrôle"):
        return UserGroupe.controle
    return None


# ── Étape 1 — Langues ──────────────────────────────────────────────────────────

async def import_langues(session, xlsx_path: str) -> None:
    df = pd.read_excel(xlsx_path)
    df.columns = [str(c).strip() for c in df.columns]

    schools = (await session.execute(select(School))).scalars().all()
    school_by_code: dict[int, School] = {
        s.code_ecole: s for s in schools if s.code_ecole is not None
    }

    updated = 0
    skipped = 0
    errors: list[str] = []

    for i, row in df.iterrows():
        code_ecole = row.get("code_ecole")
        langue = row.get("Langue")
        if pd.isna(code_ecole) or pd.isna(langue):
            errors.append(f"Ligne {i + 2} : code_ecole ou Langue manquant.")
            continue

        school = school_by_code.get(int(code_ecole))
        if school is None:
            errors.append(f"Ligne {i + 2} : aucune école trouvée pour code_ecole={int(code_ecole)}.")
            continue

        if school.langue:
            skipped += 1
            continue

        school.langue = str(langue).strip().lower()
        updated += 1

    await session.flush()
    print(f"  ✓ Langues : {updated} écoles mises à jour, {skipped} déjà renseignées, {len(errors)} erreur(s)")
    for e in errors:
        print(f"    ⚠ {e}")


# ── Étape 2 — Enseignants + groupe de recherche (Traitement / Contrôle) ───────

async def import_enseignants_et_groupes(session, xlsx_path: str) -> None:
    df = pd.read_excel(xlsx_path)
    df.columns = [str(c).strip() for c in df.columns]

    schools = (await session.execute(select(School))).scalars().all()
    school_by_code: dict[int, School] = {
        s.code_ecole: s for s in schools if s.code_ecole is not None
    }

    # Classes existantes par école (ordre alphabétique — classe_num=1 → 1ère, etc.)
    classes_rows = (
        await session.execute(select(SchoolClasse).order_by(SchoolClasse.school_id, SchoolClasse.name))
    ).scalars().all()
    classes_by_school: dict[uuid.UUID, list[str]] = {}
    for c in classes_rows:
        classes_by_school.setdefault(c.school_id, []).append(c.name)

    teachers = (
        await session.execute(select(User).where(User.role == UserRole.enseignant))
    ).scalars().all()
    # Index (nom normalisé, school_id) → liste d'enseignants (pour détecter les doublons)
    teachers_by_key: dict[tuple[str, str], list[User]] = {}
    for t in teachers:
        if t.school_id is None:
            continue
        key = (_normalize_name(t.name), str(t.school_id))
        teachers_by_key.setdefault(key, []).append(t)

    pwd_hash = hash_password(DEFAULT_PASSWORD)

    updated = 0
    created = 0
    skipped = 0
    errors: list[str] = []

    for i, row in df.iterrows():
        code_ecole = row.get("code_ecole")
        nom_enseignant = row.get("nom_enseignant")
        treat = row.get("treat")
        classe_num = row.get("classe_num")
        if pd.isna(code_ecole) or pd.isna(nom_enseignant) or pd.isna(treat):
            errors.append(f"Ligne {i + 2} : code_ecole, nom_enseignant ou treat manquant.")
            continue

        school = school_by_code.get(int(code_ecole))
        if school is None:
            errors.append(f"Ligne {i + 2} : aucune école trouvée pour code_ecole={int(code_ecole)}.")
            continue

        groupe = _map_treat(treat)
        if groupe is None:
            errors.append(f"Ligne {i + 2} : valeur 'treat' non reconnue ({treat!r}).")
            continue

        nom_clean = str(nom_enseignant).strip()
        key = (_normalize_name(nom_clean), str(school.id))
        candidates = teachers_by_key.get(key, [])

        if len(candidates) > 1:
            errors.append(f"Ligne {i + 2} : plusieurs enseignants '{nom_clean}' à l'école {school.name} — ignoré (ambigu).")
            continue

        if len(candidates) == 1:
            teacher = candidates[0]
            if teacher.groupe_recherche == groupe:
                skipped += 1
            else:
                teacher.groupe_recherche = groupe
                updated += 1
            continue

        # Aucun enseignant existant → création
        ecole_classes = classes_by_school.get(school.id, [])
        idx = int(classe_num) - 1 if not pd.isna(classe_num) else 0
        classe_assignee = [ecole_classes[idx]] if 0 <= idx < len(ecole_classes) else None

        teacher = User(
            id=uuid.uuid4(),
            name=nom_clean,
            email=None,
            phone=None,
            password_hash=pwd_hash,
            role=UserRole.enseignant,
            status=UserStatus.actif,
            must_change_password=True,
            app_access="full",
            school_id=school.id,
            classes=classe_assignee,
            groupe_recherche=groupe,
        )
        session.add(teacher)
        teachers_by_key.setdefault(key, []).append(teacher)
        created += 1

    await session.flush()
    print(
        f"  ✓ Enseignants : {created} créé(s), {updated} mis à jour, "
        f"{skipped} déjà à jour, {len(errors)} erreur(s)"
    )
    for e in errors:
        print(f"    ⚠ {e}")


# ── Main ────────────────────────────────────────────────────────────────────────

async def run(langues_path: str, assignation_path: str) -> None:
    print(f"\n{'='*60}")
    print(f"  Import Langues + Enseignants/Groupes de recherche — NDAW WUNE")
    print(f"{'='*60}")
    print(f"\n  Base de données : {os.environ.get('DATABASE_URL', '???')[:50]}…")

    async with AsyncSessionLocal() as session:
        try:
            print("\n── [1/2] Langues des écoles ─────────────────────────────")
            await import_langues(session, langues_path)

            print("\n── [2/2] Enseignants + Groupes de recherche ─────────────")
            await import_enseignants_et_groupes(session, assignation_path)

            await session.commit()
            print(f"\n{'='*60}")
            print(f"  ✅  Import terminé avec succès !")
            print(f"  Mot de passe par défaut des nouveaux enseignants : {DEFAULT_PASSWORD}")
            print(f"  (changement obligatoire à la première connexion — pensez à")
            print(f"   renseigner leur téléphone/email dans l'admin pour la connexion)")
            print(f"{'='*60}\n")

        except Exception as e:
            await session.rollback()
            print(f"\n❌  Erreur — rollback effectué : {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage : python scripts/import_langues_et_groupes_2026.py <Langues.xlsx> <Assignation.xlsx>")
        sys.exit(1)
    asyncio.run(run(sys.argv[1], sys.argv[2]))
